param([Parameter(Mandatory=$true)][string]$Binary)
$ErrorActionPreference = 'Stop'
if (-not ('SymlinkAccessTestAcl' -as [type])) {
Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class SymlinkAccessTestAcl {
    static volatile bool racing;
    static System.Threading.Thread racer;
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool MoveFileExW(string from, string to, uint flags);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string s, uint rev, out IntPtr sd, out uint size);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool SetFileSecurityW(string path, uint info, IntPtr sd);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.U1)]
    public static extern bool CreateSymbolicLinkW(string link, string target, uint flags);
    public static void StartRace(string path, string denied) {
        racing = true;
        racer = new System.Threading.Thread(() => {
            string temp = path + ".swap";
            bool link = false;
            while (racing) {
                System.IO.File.Delete(temp);
                if (link) CreateSymbolicLinkW(temp, denied, 2);
                else System.IO.File.WriteAllText(temp, "RACE-PUBLIC");
                MoveFileExW(temp, path, 1);
                link = !link;
            }
            System.IO.File.Delete(temp);
        });
        racer.Start();
    }
    public static void StopRace() {
        racing = false;
        if (racer != null) { racer.Join(); racer = null; }
    }
    public static void Set(string path, string sddl, uint flags = 0x80000004) {
        IntPtr sd; uint size;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, out sd, out size))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            if (!SetFileSecurityW(path, flags, sd))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        } finally { LocalFree(sd); }
    }
}
'@
}
$binaryPath = (Resolve-Path -LiteralPath $Binary).Path
$testParent = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.test-windows'))
[IO.Directory]::CreateDirectory($testParent) | Out-Null
$testRoot = Join-Path $testParent ([Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
$web = Join-Path $testRoot 'web'
$targets = Join-Path $testRoot 'targets'
[IO.Directory]::CreateDirectory($web) | Out-Null
[IO.Directory]::CreateDirectory($targets) | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User
[SymlinkAccessTestAcl]::Set($web, "O:$($sid.Value)", 1)
$group = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
$systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')

function Set-TestAcl([string]$Path, [string]$Kind) {
    $principal = if ($Kind -eq 'group') { $group.Value } else { $sid.Value }
    $denyAce = if ($Kind -eq 'deny') { "(D;;0x2;;;$($sid.Value))" } else { '' }
    [SymlinkAccessTestAcl]::Set($Path, "D:P${denyAce}(A;;FRFX;;;$principal)")
}

foreach ($kind in @('allow', 'deny', 'group')) {
    $path = Join-Path $targets $kind
    [IO.File]::WriteAllText($path, "BODY:$kind")
    [SymlinkAccessTestAcl]::Set($path, "O:$($sid.Value)", 1)
    Set-TestAcl $path $kind
}
foreach ($kind in @('everyone', 'empty', 'null', 'inherit_only')) {
    $path = Join-Path $targets $kind
    [IO.File]::WriteAllText($path, "BODY:$kind")
    [SymlinkAccessTestAcl]::Set($path, "O:$($sid.Value)", 1)
    $sddl = switch ($kind) {
        'everyone' { 'D:P(A;;FRFX;;;WD)' }
        'empty' { 'D:P' }
        'null' { 'D:NO_ACCESS_CONTROL' }
        'inherit_only' { "D:P(D;IO;0x2;;;$($sid.Value))(A;;FRFX;;;$($sid.Value))" }
    }
    [SymlinkAccessTestAcl]::Set($path, $sddl)
}
$fileLink = Join-Path $web 'file-link'
$fileLinkAvailable = [SymlinkAccessTestAcl]::CreateSymbolicLinkW($fileLink, (Join-Path $targets 'allow'), 2)
[IO.File]::WriteAllText((Join-Path $web 'direct'), 'DIRECT')
Set-TestAcl (Join-Path $web 'direct') 'deny' # direct file: native read allowed, projection would deny
$racePath = Join-Path $web 'race'
$junction = Join-Path $web 'link'
New-Item -ItemType Junction -Path $junction -Target $targets | Out-Null
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$prefix = $testRoot.Replace('\', '/')
$webConf = $web.Replace('\', '/')
$conf = @"
daemon off;
master_process off;
pid $prefix/server.pid;
error_log $prefix/error.log info;
events { worker_connections 64; }
http {
    access_log off;
    symlink_access_cache 2 1s;
    open_file_cache max=32 inactive=60s;
    open_file_cache_valid 1h;
    server {
        listen 127.0.0.1:$port;
        root $webConf;
        symlink_access on;
        location / { }
        location /off/ { alias $webConf/; symlink_access off; }
    }
}
"@
$configPath = Join-Path $testRoot 'server.conf'
[IO.File]::WriteAllText($configPath, $conf)
[IO.Directory]::CreateDirectory((Join-Path $testRoot 'logs')) | Out-Null
[IO.Directory]::CreateDirectory((Join-Path $testRoot 'temp')) | Out-Null
$handler = [Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(5)
$process = $null
$checks = 0
function Check-Response([string]$Path, [int]$Expected, [string]$Body = '') {
    $response = $client.GetAsync("http://127.0.0.1:$port$Path").GetAwaiter().GetResult()
    try {
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ([int]$response.StatusCode -ne $Expected) {
            throw "$Path expected $Expected got $([int]$response.StatusCode): $content"
        }
        if ($Body -ne '' -and $content -ne $Body) { throw "$Path unexpected body: $content" }
        $script:checks++
    } finally { $response.Dispose() }
}
try {
    $process = Start-Process -FilePath $binaryPath -ArgumentList @('-p', "$prefix/", '-c', $configPath) `
        -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $testRoot 'stderr.log')
    $ready = $false
    for ($i = 0; $i -lt 80; $i++) {
        if ($process.HasExited) { throw ([IO.File]::ReadAllText((Join-Path $testRoot 'stderr.log'))) }
        try { Check-Response '/direct' 200 'DIRECT'; $ready = $true; break } catch { Start-Sleep -Milliseconds 50 }
    }
    if (!$ready) { throw 'Server did not become ready' }
    Check-Response '/off/link/allow' 200 'BODY:allow'
    Check-Response '/link/allow' 200 'BODY:allow'
    Check-Response '/off/link/deny' 200 'BODY:deny'
    Check-Response '/link/deny' 403
    Check-Response '/link/everyone' 200 'BODY:everyone'
    Check-Response '/link/empty' 403
    Check-Response '/link/null' 200 'BODY:null'
    Check-Response '/link/inherit_only' 200 'BODY:inherit_only'
    if ($fileLinkAvailable) { Check-Response '/file-link' 200 'BODY:allow' }
    Check-Response '/off/link/group' 200 'BODY:group'
    Check-Response '/link/group' 403 # owner precedence in the reduced model
    Set-TestAcl (Join-Path $targets 'allow') 'deny'
    Check-Response '/link/allow' 403 # cached descriptor must not bypass the new ACL
    if ($fileLinkAvailable) { Check-Response '/file-link' 403 }
    Set-TestAcl (Join-Path $targets 'allow') 'allow'
    Check-Response '/link/allow' 200 'BODY:allow'
    for ($i = 0; $i -lt 100; $i++) {
        Check-Response '/link/allow' 200 'BODY:allow'
        Check-Response '/link/group' 403
    }
    if ($fileLinkAvailable) {
        [IO.File]::WriteAllText($racePath, 'RACE-PUBLIC')
        [SymlinkAccessTestAcl]::StartRace($racePath, (Join-Path $targets 'deny'))
        try {
            for ($i = 0; $i -lt 250; $i++) {
                $response = $client.GetAsync("http://127.0.0.1:$port/race").GetAwaiter().GetResult()
                try {
                    $status = [int]$response.StatusCode
                    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                    if ($status -notin @(200, 403) -or ($status -eq 200 -and $body -ne 'RACE-PUBLIC')) {
                        throw "Concurrent file/link replacement leaked or failed: $status $body"
                    }
                    $checks++
                } finally { $response.Dispose() }
            }
        } finally { [SymlinkAccessTestAcl]::StopRace() }
    }
    Write-Output "PASS $([IO.Path]::GetFileName($binaryPath)): $checks native Windows HTTP checks"
    if (!$fileLinkAvailable) { Write-Output 'File symlink creation unavailable; directory junction path tested.' }
} catch {
    if (Test-Path -LiteralPath (Join-Path $testRoot 'error.log')) {
        Get-Content -LiteralPath (Join-Path $testRoot 'error.log') -Tail 20 | Write-Output
    }
    throw
} finally {
    [SymlinkAccessTestAcl]::StopRace()
    if ($process -and !$process.HasExited) { Stop-Process -Id $process.Id; $process.WaitForExit() }
    $client.Dispose()
    # Remove the junction itself before recursive cleanup; never follow its target.
    if (Test-Path -LiteralPath $junction) { [IO.Directory]::Delete($junction) }
    if ($fileLinkAvailable) { [IO.File]::Delete($fileLink) }
    [IO.File]::Delete($racePath)
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    if (!$resolvedTestRoot.StartsWith($testParent + [IO.Path]::DirectorySeparatorChar)) {
        throw 'Cleanup target escaped test directory'
    }
    foreach ($fixtureFile in [IO.Directory]::GetFiles($targets)) {
        [SymlinkAccessTestAcl]::Set($fixtureFile, "D:P(A;;FA;;;$($sid.Value))")
    }
    [SymlinkAccessTestAcl]::Set((Join-Path $web 'direct'), "D:P(A;;FA;;;$($sid.Value))")
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
}
