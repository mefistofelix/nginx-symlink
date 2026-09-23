#!/usr/bin/env python3
"""Package one unified patch and check it against both pristine Git revisions."""
from pathlib import Path
import io
import subprocess
import tarfile
import tempfile

root = Path(__file__).resolve().parents[1]
nginx = root / 'upstream/nginx'
patch = root / 'patches/symlink-access.patch'
patch.parent.mkdir(exist_ok=True)
subprocess.run(['git', '-C', str(nginx), 'diff', '--check'], check=True)
unchanged = ['src/core/ngx_module.h', 'src/core/ngx_core.h',
             'src/core/ngx_open_file_cache.h', 'src/core/ngx_open_file_cache.c',
             'src/http/ngx_http_core_module.h', 'src/http/ngx_http_core_module.c',
             'src/http/ngx_http_request.h']
for name in ('nginx', 'angie'):
    assert not subprocess.check_output(['git', '-C', str(root / 'upstream' / name),
                                       'diff', '--', *unchanged]), 'Upstream ABI/core changed'
data = subprocess.check_output(['git', '-C', str(nginx), 'diff', '--binary', '-U2'])
assert b'ngx_http_symlink_access_module.c' in data and b'new file mode' in data
patch.write_bytes(data)
for name in ('nginx', 'angie'):
    repo = root / 'upstream' / name
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    archive = subprocess.check_output(['git', '-C', str(repo), 'archive', 'HEAD'])
    with tempfile.TemporaryDirectory(prefix='sa-patch-') as directory:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(directory, filter='data')
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=directory, check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=directory, check=True)
        # Check that the tested working sources agree with what the patch delivers.
        changed = subprocess.check_output(['git', '-C', str(nginx), 'diff', '--name-only']).decode().splitlines()
        for file in changed:
            delivered = (Path(directory) / file).read_bytes().replace(b'\r\n', b'\n')
            working = (repo / file).read_bytes().replace(b'\r\n', b'\n')
            assert delivered == working, f'{name}: working tree differs from delivered {file}'
    print(f'PASS identical patch applies to pristine {name} {revision}')
print(f'Created {patch} ({len(data)} bytes)')
