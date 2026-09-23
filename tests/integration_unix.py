#!/usr/bin/env python3
"""Exercise the real HTTP/file/cache integration. Run as root on Unix.

Usage: sudo python3 tests/integration_unix.py /absolute/path/to/nginx
Only temporary fixture files are chowned; no system accounts are modified.
"""
import argparse
import gzip
import http.client
import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import tempfile
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    assert os.geteuid() == 0, 'root is required for fixture ownership and worker isolation'
    account = max((p for p in pwd.getpwall() if 0 < p.pw_uid < 65534),
                  key=lambda p: len(os.getgrouplist(p.pw_name, p.pw_gid)))
    uid, primary = account.pw_uid, account.pw_gid
    memberships = os.getgrouplist(account.pw_name, primary)
    foreign = next(i for i in range(61000, 65000) if i not in memberships)
    unknown = next(i for i in range(61000, 65000)
                   if all(p.pw_uid != i for p in pwd.getpwall()))
    with tempfile.TemporaryDirectory(prefix='symlink-access-') as tmp:
        base = Path(tmp)
        root = base / 'web'
        target = base / 'targets'
        root.mkdir(); target.mkdir()
        os.chown(root, uid, primary)
        unknown_root = base / 'unknown'; unknown_root.mkdir()
        os.chown(unknown_root, unknown, foreign)
        def file(name, owner, group, mode, body=None):
            p = target / name
            p.write_bytes(body or ('BODY:' + name).encode())
            os.chown(p, owner, group); p.chmod(mode)
            return p
        def link(name, dest):
            p = root / name
            p.symlink_to(dest)
            return p
        good = file('allow', unknown, primary, 0o640)
        bad = file('deny', unknown, foreign, 0o640)
        world = file('world', unknown, foreign, 0o604)
        ownerdeny = file('ownerdeny', uid, primary, 0o044)
        groupdeny = file('groupdeny', unknown, primary, 0o604)
        supplementary = next((g for g in memberships if g != primary), None)
        if supplementary is not None:
            link('supplementary', file('supplementary', unknown, supplementary, 0o640))
        for p in (good, bad, world, ownerdeny, groupdeny): link(p.name, p)
        link('chain', root / 'allow')
        link('broken', target / 'missing')
        link('loop', root / 'loop')
        link('dir', target)
        link('mutable', good)
        link('race', good)
        link('owned', file('owned-target', uid, foreign, 0o400))
        direct = root / 'direct'; direct.write_text('DIRECT'); direct.chmod(0)
        (target / 'index.html').symlink_to(bad)
        gz = file('compressed.gz', unknown, foreign, 0o640, gzip.compress(b'SECRET-GZIP'))
        link('compressed.gz', gz)
        (root / 'compressed').write_text('PUBLIC-FALLBACK')
        (root / 'listing').mkdir()
        (root / 'listing' / 'a').write_text('a')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        conf = base / 'server.conf'
        conf.write_text(f'''
daemon off;
master_process off;
user root;
pid {base}/server.pid;
error_log {base}/error.log notice;
events {{ worker_connections 128; }}
http {{
    client_body_temp_path {base}/client_temp;
    proxy_temp_path {base}/proxy_temp;
    fastcgi_temp_path {base}/fastcgi_temp;
    uwsgi_temp_path {base}/uwsgi_temp;
    scgi_temp_path {base}/scgi_temp;
    access_log off;
    symlink_access_cache max=2 valid=1s negative_valid=1s;
    open_file_cache max=128 inactive=60s;
    open_file_cache_valid 1h;
    open_file_cache_errors on;
    sendfile on;
    server {{
        listen 127.0.0.1:{port};
        set $site_root {root};
        root $site_root;
        access_log {base}/access-$server_port.log;
        symlink_access root_owner;
        location / {{ }}
        location /off/ {{ symlink_access off; alias {root}/; }}
        location /alias/ {{ alias {root}/; symlink_access_root {root}; }}
        location /unknown/ {{ alias {root}/; symlink_access_root {unknown_root}; }}
        location /try/ {{ alias {root}/; try_files $uri =404; }}
        location = /redirect {{ rewrite ^ /deny last; }}
        location /blocked/ {{ alias {root}/; disable_symlinks on; }}
        location /sameowner/ {{ alias {root}/; disable_symlinks if_not_owner; }}
        location /from/ {{ alias {root}/; disable_symlinks on from={root}; }}
        location /listing/ {{ autoindex on; }}
        location /random/ {{ alias {root}/listing/; random_index on; }}
        location /dav/ {{ alias {root}/; dav_methods PUT DELETE; }}
        location = /compressed {{ gzip_static on; }}
        location /nocache/ {{ alias {root}/; open_file_cache off; }}
    }}
}}
''')
        config_test = subprocess.run([str(binary), '-t', '-p', tmp + '/', '-c', str(conf)],
                                     capture_output=True, text=True)
        assert config_test.returncode == 0, config_test.stderr
        proc = subprocess.Popen([str(binary), '-p', tmp + '/', '-c', str(conf)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def request(path, method='GET', headers=None):
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                c.request(method, path, headers=headers or {})
                r = c.getresponse()
                return r.status, r.read()
            finally: c.close()
        passed = 0
        def check(path, status, body=None, **kw):
            nonlocal passed
            got, data = request(path, **kw)
            assert got == status, (path, status, got, data[:100])
            if body is not None: assert data == body, (path, data)
            passed += 1
        try:
            for _ in range(100):
                if proc.poll() is not None: raise AssertionError(proc.stderr.read().decode())
                try:
                    request('/direct'); break
                except OSError: time.sleep(.02)
            else: raise AssertionError('startup timeout')
            check('/direct', 200, b'DIRECT')
            check('/off/allow', 200, b'BODY:allow')
            check('/allow', 200, b'BODY:allow')
            check('/deny', 403)
            check('/world', 200)
            check('/ownerdeny', 403)
            check('/groupdeny', 403)
            if supplementary is not None: check('/supplementary', 200)
            check('/owned', 200)
            check('/chain', 200)
            check('/broken', 404)
            check('/loop', 403)
            check('/direct', 200, b'DIRECT')
            check('/dir/deny', 403)
            check('/dir/allow', 200)
            check('/dir/', 403)
            check('/alias/deny', 403)
            check('/alias/allow', 200)
            check('/try/allow', 200)
            check('/try/deny', 404)
            check('/unknown/world', 403)
            check('/redirect', 403)
            check('/blocked/allow', 403)
            check('/from/allow', 403)
            check('/sameowner/allow', 403)
            check('/off/deny', 200)
            check('/deny', 403)  # same pathname already cached by the off location
            check('/nocache/deny', 403)
            check('/listing/', 403)
            check('/random/', 403)
            check('/dav/new', 403, method='PUT')
            check('/allow', 200, b'', method='HEAD')
            check('/allow', 206, b'BODY', headers={'Range': 'bytes=0-3'})
            status, body = request('/compressed', headers={'Accept-Encoding': 'gzip'})
            assert status in (200, 403) and body != gz.read_bytes(), (status, body)
            passed += 1
            check('/mutable', 200)
            good.chmod(0o600)
            check('/mutable', 403)
            good.chmod(0o640)
            check('/mutable', 200)
            (root / 'replacement').symlink_to(bad)
            os.replace(root / 'replacement', root / 'mutable')
            check('/mutable', 403)
            # Missing identity must not be confused with a valid empty group list.
            check('/unknown/world', 403)
            time.sleep(1.1)
            check('/unknown/world', 403)
            stop = threading.Event()
            errors = []
            def swap():
                try:
                    while not stop.is_set():
                        for dest in (bad, good):
                            (root / 'swap').symlink_to(dest)
                            os.replace(root / 'swap', root / 'race')
                except BaseException as e: errors.append(e)
            thread = threading.Thread(target=swap); thread.start()
            try:
                for _ in range(250):
                    status, body = request('/race')
                    assert status in (200, 403), status
                    assert status != 200 or body == b'BODY:allow', body
            finally:
                stop.set(); thread.join()
            assert not errors, errors
            passed += 250
            # Requests must not accumulate descriptors, including denied requests.
            fd_dir = Path(f'/proc/{proc.pid}/fd')
            if fd_dir.exists():
                before = len(list(fd_dir.iterdir()))
                for _ in range(150): request('/deny'); request('/allow')
                after = len(list(fd_dir.iterdir()))
                assert after <= before + 2, (before, after)
                passed += 1
            print(f'PASS {binary.name}: {passed} checks (including 250 concurrent retarget checks)')
            assert (base / f'access-{port}.log').stat().st_size > 0
        except BaseException:
            print((base / 'error.log').read_text())
            raise
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=5)

if __name__ == '__main__': main()
