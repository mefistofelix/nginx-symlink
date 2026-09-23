/* Tests the actual common policy/cache with deterministic identity lookups. */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_symlink_access.h>
#include <assert.h>

static ngx_time_t test_time;
volatile ngx_time_t *ngx_cached_time = &test_time;
volatile ngx_cycle_t *ngx_cycle;
static unsigned user = 1, owner = 9, gid = 7;
static unsigned calls, grant = 1, lookup_ok = 1, usable = 1;
static unsigned owner_bits, group_bits = 4, other_bits;

void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
#ifdef ngx_src_file
    const char *filename,
#endif
    ngx_err_t err,
    const char *fmt, ...)
{
    (void) level; (void) log; (void) err; (void) fmt;
#ifdef ngx_src_file
    (void) filename;
#endif
}

ngx_fd_t ngx_sa_open_root(ngx_str_t *root)
{
    (void) root;
    return open("/dev/null", O_RDONLY);
}

ngx_int_t ngx_sa_get_user_groups(ngx_pool_t *pool, ngx_sa_id_t *id,
    ngx_sa_id_t **groups, ngx_uint_t *n)
{
    (void) id;
    calls++;
    *n = 0; *groups = NULL;
    if (!lookup_ok) { return NGX_DECLINED; }
    if (!grant) { return NGX_OK; }
    *groups = ngx_pcalloc(pool, sizeof(ngx_sa_id_t));
    assert(*groups != NULL);
    assert(ngx_sa_id_copy(pool, *groups, &gid, sizeof(gid)) == NGX_OK);
    *n = 1;
    return NGX_OK;
}

ngx_int_t ngx_sa_get_path_access(ngx_pool_t *pool, ngx_fd_t fd, ngx_sa_access_t *a)
{
    unsigned *id = fd == 777 ? &owner : &user;
    ngx_memzero(a, sizeof(*a));
    assert(ngx_sa_id_copy(pool, &a->owner, id, sizeof(*id)) == NGX_OK);
    a->groups = ngx_pcalloc(pool, sizeof(ngx_sa_group_access_t));
    assert(a->groups != NULL);
    assert(ngx_sa_id_copy(pool, &a->groups[0].id, &gid, sizeof(gid)) == NGX_OK);
    a->n = 1; a->groups[0].bits = group_bits;
    a->owner_bits = owner_bits; a->other_bits = other_bits;
    a->usable = usable;
    return NGX_OK;
}

int main(void)
{
    ngx_log_t log;
    ngx_pool_t *pool;
    ngx_symlink_access_t ctx;
    unsigned previous;
    ngx_memzero(&log, sizeof(log));
    ngx_memzero(&ctx, sizeof(ctx));
    ngx_pagesize = 4096;
    pool = ngx_create_pool(4096, &log);
    assert(pool != NULL);
    ctx.pool = pool;
    ctx.cache = ngx_sa_cache_create(pool, 1, 10, 2);
    assert(ctx.cache != NULL);
    test_time.sec = 100;
    assert(ngx_sa_check(&ctx, 777) == NGX_OK && calls == 1);
    grant = 0;
    test_time.sec = 109;
    assert(ngx_sa_check(&ctx, 777) == NGX_OK && calls == 1);
    test_time.sec = 110;
    assert(ngx_sa_check(&ctx, 777) == NGX_DECLINED && calls == 2);
    /* Hit at 109 did not slide expiry beyond 110. */
    user = 2; lookup_ok = 0;
    assert(ngx_sa_check(&ctx, 777) == NGX_DECLINED && calls == 3);
    lookup_ok = 1; grant = 1;
    test_time.sec = 111;
    assert(ngx_sa_check(&ctx, 777) == NGX_DECLINED && calls == 3);
    test_time.sec = 112;
    assert(ngx_sa_check(&ctx, 777) == NGX_OK && calls == 4);
    user = 3;
    assert(ngx_sa_check(&ctx, 777) == NGX_OK && calls == 5);
    user = 1;
    assert(ngx_sa_check(&ctx, 777) == NGX_OK && calls == 6); /* collision eviction */
    group_bits = 0; other_bits = 4;
    assert(ngx_sa_check(&ctx, 777) == NGX_DECLINED); /* no other fallback */
    owner = user; group_bits = 4; owner_bits = 0;
    assert(ngx_sa_check(&ctx, 777) == NGX_DECLINED); /* owner precedence */
    owner_bits = 4;
    assert(ngx_sa_check(&ctx, 777) == NGX_OK);
    usable = 0;
    assert(ngx_sa_check(&ctx, 777) == NGX_DECLINED);
    usable = 1; owner = 9;
    ctx.cache = NULL; grant = 0;
    previous = calls;
    assert(ngx_sa_check(&ctx, 777) == NGX_OK); /* other, valid empty group list */
    assert(ngx_sa_check(&ctx, 777) == NGX_OK && calls == previous + 2);
    lookup_ok = 0;
    assert(ngx_sa_check(&ctx, 777) == NGX_DECLINED); /* failed != empty */
    ngx_destroy_pool(pool);
    puts("PASS cache/policy: TTL, negative TTL, collisions, cache off, precedence, denial");
    return 0;
}
