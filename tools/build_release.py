#!/usr/bin/env python3
"""Build the pinned releases using configure options from official binaries."""
from pathlib import Path
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'tools/releases.json').read_text())
TARGET = sys.argv[1]
assert TARGET in ('linux', 'windows')
WIN = TARGET == 'windows'
DIST = ROOT / 'dist'
DIST.mkdir(exist_ok=True)
(ROOT / '.build').mkdir(exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix=TARGET + '-', dir=ROOT / '.build'))
BASH = 'C:/Program Files/Git/bin/bash.exe' if WIN else 'bash'


def run(args, cwd=None, capture=False):
    return subprocess.run([str(a) for a in args], cwd=cwd, check=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT if capture else None,
                          text=True).stdout


def download(spec, name):
    path = WORK / name
    urllib.request.urlretrieve(spec[0], path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == spec[1], name + ': hash mismatch'
    return path


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as z:
            z.extractall(destination)
    else:
        with tarfile.open(archive) as t:
            t.extractall(destination, filter='data')


def options(output):
    return shlex.split(output.split('configure arguments: ', 1)[1].strip())


def features(args):
    # Includes module switches AND features such as threads, AIO, compat and JIT.
    build_only = ('--with-cc=', '--with-cc-opt=', '--with-ld-opt=',
                  '--with-pcre=', '--with-zlib=', '--with-openssl=', '--with-openssl-opt=')
    return sorted(a for a in args if a.startswith(('--with-', '--without-', '--add-'))
                  and not a.startswith(build_only))


def modules(source, builddir):
    text = (source / builddir / 'ngx_modules.c').read_text()
    return re.findall(r'^    &(ngx_\w+_module),$', text, re.M)


for server in (['nginx'] if WIN else ['nginx', 'angie']):
    spec = MANIFEST[server]
    version = spec['version']
    name = f'{server}-{version}-{TARGET}-' + ('x86' if WIN else 'x86_64')
    official = WORK / (server + '-official')
    archive = download(spec[TARGET], server + ('.zip' if WIN else '.deb'))
    if WIN:
        extract(archive, official)
        reference = official / f'nginx-{version}' / 'nginx.exe'
    else:
        run(['dpkg-deb', '-x', archive, official])
        reference = official / 'usr/sbin' / server
    reference_v = run([reference, '-V'], capture=True)
    reference_args = options(reference_v)
    source_archive = download(spec['source'], server + '.tar.gz')
    extract(source_archive, WORK)
    source = WORK / f'{server}-{version}'
    # A separate repository prevents git apply from treating .build as a prefix.
    run(['git', 'init', '-q'], source)
    args = [a for a in reference_args if not a.startswith('--feature-cache=')]
    builddir = next((a.split('=', 1)[1] for a in args if a.startswith('--builddir=')), 'objs')
    if WIN:
        for library, library_spec in MANIFEST['windows_libraries'].items():
            extract(download(library_spec, library + '.tar.gz'), source / builddir / 'lib')
    configure = source / ('auto/configure' if (source / 'auto/configure').exists() else 'configure')
    original_header = WORK / (server + '-ngx_module.h')
    shutil.copyfile(source / 'src/core/ngx_module.h', original_header)
    run([BASH, configure.relative_to(source).as_posix(), *args], source)
    original_modules = modules(source, builddir)
    assert original_modules, 'No module table found'
    run(['git', 'apply', '--check', ROOT / 'patches/symlink-access.patch'], source)
    run(['git', 'apply', ROOT / 'patches/symlink-access.patch'], source)
    assert (source / 'src/core/ngx_module.h').read_bytes() == original_header.read_bytes()
    run([BASH, configure.relative_to(source).as_posix(), *args], source)
    patched_modules = modules(source, builddir)
    added = 'ngx_http_symlink_access_module'
    assert patched_modules.count(added) == 1
    assert [m for m in patched_modules if m != added] == original_modules, 'Module table changed'
    run(['nmake', '-f', f'{builddir}/Makefile'] if WIN else
        ['make', '-f', f'{builddir}/Makefile', '-j', str(min(os.cpu_count() or 2, 4))], source)
    binary = source / builddir / (server + ('.exe' if WIN else ''))
    built_v = run([binary, '-V'], capture=True)
    assert features(options(built_v)) == features(reference_args), 'Official feature options changed'
    if WIN:
        run(['pwsh', '-NoProfile', '-File', ROOT / 'tests/integration_windows.ps1', '-Binary', binary])
    else:
        run(['sudo', sys.executable, ROOT / 'tests/integration_unix.py', binary])
        run([sys.executable, ROOT / 'tests/module_abi.py', source, original_header])
        run([BASH, ROOT / 'tests/run_cache_policy.sh', source])
    package = WORK / name
    package.mkdir()
    shutil.copy2(binary, package / binary.name)
    shutil.copytree(source / 'conf', package / 'conf')
    shutil.copytree(source / 'html', package / 'html')
    (package / 'logs').mkdir()
    (package / 'temp').mkdir()
    shutil.copy2(source / 'LICENSE', package / 'LICENSE')
    shutil.copy2(ROOT / 'LICENSE', package / 'SYMLINK-ACCESS-LICENSE')
    shutil.copy2(ROOT / 'patches/symlink-access.patch', package / 'symlink-access.patch')
    shutil.copy2(ROOT / 'README.md', package / 'README.md')
    (package / 'official-V.txt').write_text(reference_v)
    (package / 'patched-V.txt').write_text(built_v)
    report = dict(server=server, version=version, platform=TARGET,
                  reference_url=spec[TARGET][0], reference_sha256=spec[TARGET][1],
                  source_url=spec['source'][0], source_sha256=spec['source'][1],
                  features=features(reference_args), official_modules=original_modules,
                  patched_modules=patched_modules, only_addition=added,
                  scope='Built-in modules of the official base binary; separately packaged dynamic modules excluded.')
    report_path = DIST / (name + '-modules.json')
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    shutil.copy2(report_path, package / 'modules.json')
    if WIN:
        licenses = package / 'licenses'
        licenses.mkdir()
        for library in MANIFEST['windows_libraries']:
            for filename in ('LICENSE', 'LICENCE', 'LICENSE.txt', 'COPYING'):
                path = source / builddir / 'lib' / library / filename
                if path.exists():
                    shutil.copy2(path, licenses / (library + '-' + filename))
        shutil.make_archive(str(DIST / name), 'zip', WORK, name)
    else:
        run(['strip', package / server])
        (package / 'dependencies.txt').write_text(run(['ldd', package / server], capture=True))
        with tarfile.open(DIST / (name + '.tar.gz'), 'w:gz') as t:
            t.add(package, arcname=name)
    print(f'PASS {name}: {len(original_modules)} upstream modules preserved; symlink_access added', flush=True)
