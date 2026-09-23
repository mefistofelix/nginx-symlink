#!/bin/sh
set -eu
src=$(cd "$1" && pwd)
tests=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=$(mktemp -d)
trap 'rm -f "$out/test"; rmdir "$out"' EXIT HUP INT TERM
cc -O1 -g -ffunction-sections -fdata-sections -Wall -Wextra \
   -Wno-unused-parameter -Werror -fsanitize=address,undefined \
   -I "$src/objs" -I "$src/src/core" -I "$src/src/os/unix" \
   -I "$src/src/event" -I "$src/src/event/modules" \
   -I "$src/src/event/quic" \
   "$tests/cache_policy.c" "$src/src/core/ngx_symlink_access.c" \
   "$src/src/core/ngx_palloc.c" "$src/src/core/ngx_rbtree.c" \
   "$src/src/core/ngx_string.c" "$src/src/core/ngx_crc32.c" \
   "$src/src/os/unix/ngx_alloc.c" -Wl,--gc-sections -o "$out/test"
"$out/test"
