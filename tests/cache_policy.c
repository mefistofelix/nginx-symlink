/* Exercise the production module, substituting only NSS identity lookups. */
#include "ngx_http_symlink_access_module.c"
#include <assert.h>

static ngx_time_t test_time;
volatile ngx_time_t *ngx_cached_time = &test_time;
volatile ngx_cycle_t *ngx_cycle;
static unsigned calls, grant = 1, lookup_ok = 1;

void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
#ifdef ngx_src_file
    const char *filename,
#endif
    ngx_err_t err, const char *fmt, ...)
{
    (void) level; (void) log; (void) err; (void) fmt;
#ifdef ngx_src_file
    (void) filename;
#endif
}

int getpwuid_r(uid_t uid, struct passwd *pw, char *buf, size_t size, struct passwd **result)
{
    (void) uid; (void) buf; (void) size;
    calls++;
    *result = lookup_ok ? pw : NULL;
    pw->pw_name = "fixture";
    pw->pw_gid = getgid();
    return 0;
}

int getgrouplist(const char *name, gid_t gid, gid_t *groups, int *n)
{
    (void) name;
    assert(*n >= 1);
    *n = 1;
    groups[0] = grant ? gid : gid + 1;
    return 1;
}

int main(void)
{
    ngx_log_t log = {0};
    ngx_pool_t *pool;
    ngx_sa_entry_t *slots[1] = {NULL};
    ngx_sa_cache_t cache = {1, 10, slots};
    ngx_sa_id_t owner = getuid() + 1;
    ngx_sa_user_t *user;
    unsigned previous;
    FILE *file = tmpfile();
    int fd;
    assert(file);
    fd = fileno(file);
    assert(fchmod(fd, 0044) == 0);
    ngx_pagesize = 4096;
    pool = ngx_create_pool(4096, &log);
    assert(pool);
    test_time.sec = 100;
    user = ngx_sa_cached_user(&cache, pool, &owner);
    assert(user && calls == 1 && (ngx_sa_permissions(pool, fd, user) & 4));
    grant = 0;
    test_time.sec = 109;
    assert(ngx_sa_cached_user(&cache, pool, &owner) == user && calls == 1);
    test_time.sec = 110;
    user = ngx_sa_cached_user(&cache, pool, &owner);
    assert(user && calls == 2 && user->groups->ids[0] == getgid() + 1);
    /* Absolute TTL: the hit at 109 did not postpone expiry. */
    owner++;
    lookup_ok = 0;
    assert(!ngx_sa_cached_user(&cache, pool, &owner) && calls == 3);
    lookup_ok = 1;
    grant = 1;
    assert(ngx_sa_cached_user(&cache, pool, &owner) && calls == 4);
    owner++;
    assert(ngx_sa_cached_user(&cache, pool, &owner) && calls == 5);
    owner--;
    user = ngx_sa_cached_user(&cache, pool, &owner);
    assert(user && calls == 6); /* collision evicted the full identity */
    assert(fchmod(fd, 0004) == 0);
    assert(!(ngx_sa_permissions(pool, fd, user) & 4)); /* group wins over other */
    user->owner = getuid();
    assert(fchmod(fd, 0044) == 0);
    assert(!(ngx_sa_permissions(pool, fd, user) & 4)); /* owner wins over group */
    assert(fchmod(fd, 0400) == 0);
    assert(ngx_sa_permissions(pool, fd, user) & 4);
    ngx_sa_cleanup(&cache);
    cache.max = 0;
    previous = calls;
    assert(ngx_sa_cached_user(&cache, pool, &owner));
    assert(ngx_sa_cached_user(&cache, pool, &owner) && calls == previous + 2);
    lookup_ok = 0;
    assert(!ngx_sa_cached_user(&cache, pool, &owner));
    fclose(file);
    ngx_destroy_pool(pool);
    puts("PASS cache/policy: absolute TTL, collisions, cache off, lookup failure, precedence");
    return 0;
}
