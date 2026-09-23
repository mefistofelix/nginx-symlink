#!/usr/bin/env python3
"""Verify that both original and patched-header dynamic modules load."""
from pathlib import Path
import subprocess
import sys
import tempfile

src = Path(sys.argv[1]).resolve()
binary = src / 'objs' / ('angie' if (src / 'src/core/angie.h').exists() else 'nginx')
code = '''
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>
static ngx_http_module_t ctx = {0};
ngx_module_t ngx_http_sa_probe_module = {
    NGX_MODULE_V1, &ctx, NULL, NGX_HTTP_MODULE,
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NGX_MODULE_V1_PADDING
};
ngx_module_t *ngx_modules[] = { &ngx_http_sa_probe_module, NULL };
char *ngx_module_names[] = { "ngx_http_sa_probe_module", NULL };
char *ngx_module_order[] = { NULL };
'''
with tempfile.TemporaryDirectory(prefix='sa-abi-') as tmp:
    p = Path(tmp)
    (p / 'logs').mkdir()
    (p / 'module.c').write_text(code)
    for legacy in (False, True):
        include = []
        if legacy:
            (p / 'ngx_module.h').write_bytes(Path(sys.argv[2]).read_bytes() if len(sys.argv) > 2
                else subprocess.check_output(['git', '-C', str(src), 'show', 'HEAD:src/core/ngx_module.h']))
            include = ['-I', str(p)]
        args = ['cc', '-shared', '-fPIC', '-Wall', '-Werror'] + include
        for directory in ['objs', 'src/core', 'src/os/unix', 'src/event',
                          'src/event/modules', 'src/event/quic', 'src/http',
                          'src/http/modules', 'src/http/v2', 'src/http/v3']:
            args += ['-I', str(src / directory)]
        so = p / ('legacy.so' if legacy else 'patched.so')
        subprocess.run(args + [str(p / 'module.c'), '-o', str(so)], check=True)
        conf = p / 'server.conf'
        conf.write_text(f'load_module {so};\nerror_log stderr;\npid {p}/server.pid;\nevents {{}}\nhttp {{\n'
                        + ''.join(f'{kind}_temp_path {p}/{kind};\n' for kind in
                                  ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi')) + '}\n')
        result = subprocess.run([str(binary), '-t', '-p', tmp+'/', '-c', str(conf)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
print(f'PASS {binary.name}: original and patched-header dynamic modules accepted')
