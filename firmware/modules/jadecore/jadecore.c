// jadecore: Jade's native helpers for MicroPython.
//
//   search(root, query, max_hits=8, snip=160) -> [(path, score, line, snippet), ...]
//   scan(path, kinds=31)       -> [(kind, a, b, n, line), ...]   (HEADING/LINK/TAG/TASK/META)
//   strip(text)                -> plain text for a model prompt
//   terms(query)               -> the search terms a query becomes
//   VfsJade(prefix="")         -> filesystem object (see jade_vfs.c)
//   bridge_take()              -> (id, request) or None          (shell → Python requests)
//   bridge_reply(id, text, status=0) -> True if delivered
//   bridge_pending()           -> number of requests waiting
//   bridge_call(request, timeout_ms=5000) -> (status, reply)     (what a shell command does)
//   shell(line)                -> (status, output)               (run one BreezyBox command)
//   platform                   -> "esp32" or "host"
#include <string.h>
#include <stdlib.h>

#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/mpthread.h"
#include "py/objstr.h"

#include "jade_idx.h"
#include "jade_bridge.h"
#include "jade_vfs.h"

extern const mp_obj_type_t jade_vfs_type;

// Run one shell command line, capturing its output (up to cap bytes; *len gets the length).
// The ESP32 board runs it through BreezyBox; the host default runs /bin/sh (tests only).
// Returns the exit status, or -1 if commands can't run here.
__attribute__((weak)) int jade_platform_shell(const char *line, char *out, size_t cap, size_t *len) {
    *len = 0;
    #ifdef __unix__
    FILE *p = popen(line, "r");
    if (!p) {
        return -1;
    }
    size_t n;
    while (*len < cap && (n = fread(out + *len, 1, cap - *len, p)) > 0) {
        *len += n;
    }
    char sink[256];
    while (fread(sink, 1, sizeof(sink), p) > 0) {
    }
    int st = pclose(p);
    return st < 0 ? -1 : (st >> 8) & 0xff;
    #else
    (void)line;
    (void)out;
    (void)cap;
    return -1;
    #endif
}

// The real path of a vault path ("/root/vault" → prefix + "/root/vault"), and back.
static const char *to_real(const char *path, char *buf, size_t cap) {
    if (!jade_vfs_prefix[0] || path[0] != '/') {
        return path;
    }
    snprintf(buf, cap, "%s%s", jade_vfs_prefix, path);
    return buf;
}
static const char *from_real(const char *path) {
    size_t pl = strlen(jade_vfs_prefix);
    if (pl && strncmp(path, jade_vfs_prefix, pl) == 0 && path[pl] == '/') {
        return path + pl;
    }
    return path;
}

static volatile int s_search_busy = 0;   // jade_idx keeps static state; one search at a time

static mp_obj_t jc_search(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_root, ARG_query, ARG_max_hits, ARG_snip };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_root, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_query, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_max_hits, MP_ARG_INT, {.u_int = 8} },
        { MP_QSTR_snip, MP_ARG_INT, {.u_int = 160} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    int max_hits = args[ARG_max_hits].u_int;
    if (max_hits < 1 || max_hits > 50) {
        mp_raise_ValueError(MP_ERROR_TEXT("max_hits must be 1..50"));
    }
    char rootbuf[JIDX_PATH_MAX];
    const char *root = to_real(mp_obj_str_get_str(args[ARG_root].u_obj), rootbuf, sizeof(rootbuf));
    const char *query = mp_obj_str_get_str(args[ARG_query].u_obj);
    // copy the query: the GIL is released below and the string object could move
    char q[256];
    strncpy(q, query, sizeof(q) - 1);
    q[sizeof(q) - 1] = '\0';
    char r[JIDX_PATH_MAX];
    strncpy(r, root, sizeof(r) - 1);
    r[sizeof(r) - 1] = '\0';

    jidx_hit_t *hits = m_new(jidx_hit_t, max_hits);
    jidx_stats_t stats;
    if (s_search_busy) {
        m_del(jidx_hit_t, hits, max_hits);
        mp_raise_OSError(MP_EBUSY);
    }
    s_search_busy = 1;
    MP_THREAD_GIL_EXIT();
    int n = jidx_search(r, q, hits, max_hits, args[ARG_snip].u_int, &stats);
    MP_THREAD_GIL_ENTER();
    s_search_busy = 0;
    if (n < 0) {
        m_del(jidx_hit_t, hits, max_hits);
        mp_raise_OSError(MP_ENOENT);
    }
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        mp_obj_t t[4] = {
            mp_obj_new_str_from_cstr(from_real(hits[i].path)),
            MP_OBJ_NEW_SMALL_INT(hits[i].score),
            MP_OBJ_NEW_SMALL_INT(hits[i].line),
            mp_obj_new_str_from_cstr(hits[i].snippet),
        };
        mp_obj_list_append(list, mp_obj_new_tuple(4, t));
    }
    m_del(jidx_hit_t, hits, max_hits);
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(jc_search_obj, 2, jc_search);

static void scan_cb(void *ctx, int kind, const char *a, size_t al, const char *b, size_t bl, int n, int line) {
    mp_obj_t t[5] = {
        MP_OBJ_NEW_SMALL_INT(kind),
        mp_obj_new_str(a, al),
        mp_obj_new_str(b, bl),
        MP_OBJ_NEW_SMALL_INT(n),
        MP_OBJ_NEW_SMALL_INT(line),
    };
    mp_obj_list_append((mp_obj_t)ctx, mp_obj_new_tuple(5, t));
}

static mp_obj_t jc_scan(size_t n_args, const mp_obj_t *args) {
    char buf[JIDX_PATH_MAX];
    const char *path = to_real(mp_obj_str_get_str(args[0]), buf, sizeof(buf));
    int kinds = n_args > 1 ? mp_obj_get_int(args[1]) : 31;
    mp_obj_t list = mp_obj_new_list(0, NULL);
    // runs with the GIL held: the callback builds Python objects
    if (jidx_scan(path, kinds, scan_cb, list) != 0) {
        mp_raise_OSError(MP_ENOENT);
    }
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(jc_scan_obj, 1, 2, jc_scan);

static mp_obj_t jc_strip(mp_obj_t text_in) {
    size_t len;
    const char *text = mp_obj_str_get_data(text_in, &len);
    vstr_t v;
    vstr_init_len(&v, len);
    size_t n = jidx_strip(text, len, v.buf, len);
    v.len = n;
    return mp_obj_new_str_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_1(jc_strip_obj, jc_strip);

static mp_obj_t jc_terms(mp_obj_t q_in) {
    char terms[JIDX_MAX_TERMS][JIDX_TERM_MAX + 1];
    int n = jidx_terms(mp_obj_str_get_str(q_in), terms, JIDX_MAX_TERMS);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        mp_obj_list_append(list, mp_obj_new_str_from_cstr(terms[i]));
    }
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(jc_terms_obj, jc_terms);

// ---------------------------------------------------------------- bridge
static mp_obj_t jc_bridge_take(void) {
    char buf[JBRIDGE_REQ_MAX];
    int id = jbridge_take(buf, sizeof(buf));
    if (!id) {
        return mp_const_none;
    }
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(id), mp_obj_new_str_from_cstr(buf) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(jc_bridge_take_obj, jc_bridge_take);

static mp_obj_t jc_bridge_reply(size_t n_args, const mp_obj_t *args) {
    size_t len;
    const char *text = mp_obj_str_get_data(args[1], &len);
    int status = n_args > 2 ? mp_obj_get_int(args[2]) : 0;
    return mp_obj_new_bool(jbridge_reply(mp_obj_get_int(args[0]), status, text, len) == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(jc_bridge_reply_obj, 2, 3, jc_bridge_reply);

static mp_obj_t jc_bridge_pending(void) {
    return MP_OBJ_NEW_SMALL_INT(jbridge_pending());
}
static MP_DEFINE_CONST_FUN_OBJ_0(jc_bridge_pending_obj, jc_bridge_pending);

static mp_obj_t jc_bridge_call(size_t n_args, const mp_obj_t *args) {
    char req[JBRIDGE_REQ_MAX];
    strncpy(req, mp_obj_str_get_str(args[0]), sizeof(req) - 1);
    req[sizeof(req) - 1] = '\0';
    unsigned timeout = n_args > 1 ? (unsigned)mp_obj_get_int(args[1]) : 5000;
    char *reply = NULL;
    MP_THREAD_GIL_EXIT();
    int st = jbridge_call(req, timeout, &reply);
    MP_THREAD_GIL_ENTER();
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(st), reply ? mp_obj_new_str_from_cstr(reply) : mp_const_none };
    free(reply);
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(jc_bridge_call_obj, 1, 2, jc_bridge_call);

// ---------------------------------------------------------------- shell
// Make arbitrary command output valid UTF-8: bad bytes become '?', a character cut off at the end
// is dropped. Returns the new length.
static size_t utf8_clean(char *s, size_t len) {
    size_t i = 0;
    while (i < len) {
        unsigned char c = (unsigned char)s[i];
        size_t need = c < 0x80 ? 1 : (c & 0xE0) == 0xC0 ? 2 : (c & 0xF0) == 0xE0 ? 3 : (c & 0xF8) == 0xF0 ? 4 : 0;
        if (need == 0) {
            s[i++] = '?';
            continue;
        }
        if (i + need > len) {
            return i;   // cut-off character at the end
        }
        size_t k = 1;
        while (k < need && ((unsigned char)s[i + k] & 0xC0) == 0x80) {
            k++;
        }
        if (k < need) {
            s[i++] = '?';
            continue;
        }
        i += need;
    }
    return len;
}

#define SHELL_OUT_MAX 8192
static mp_obj_t jc_shell(mp_obj_t line_in) {
    char line[384];
    size_t ll;
    const char *l = mp_obj_str_get_data(line_in, &ll);
    if (ll >= sizeof(line)) {
        mp_raise_ValueError(MP_ERROR_TEXT("command too long"));
    }
    memcpy(line, l, ll + 1);
    char *out = malloc(SHELL_OUT_MAX);
    if (!out) {
        mp_raise_OSError(MP_ENOMEM);
    }
    size_t len = 0;
    MP_THREAD_GIL_EXIT();
    int st = jade_platform_shell(line, out, SHELL_OUT_MAX, &len);
    MP_THREAD_GIL_ENTER();
    len = utf8_clean(out, len);
    vstr_t v;
    vstr_init_len(&v, len);
    memcpy(v.buf, out, len);
    free(out);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(st), mp_obj_new_str_from_vstr(&v) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(jc_shell_obj, jc_shell);

static mp_obj_t jc_init(void) {
    jbridge_init();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(jc_init_obj, jc_init);

static const mp_rom_map_elem_t jadecore_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_jadecore) },
    { MP_ROM_QSTR(MP_QSTR___init__), MP_ROM_PTR(&jc_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_search), MP_ROM_PTR(&jc_search_obj) },
    { MP_ROM_QSTR(MP_QSTR_scan), MP_ROM_PTR(&jc_scan_obj) },
    { MP_ROM_QSTR(MP_QSTR_strip), MP_ROM_PTR(&jc_strip_obj) },
    { MP_ROM_QSTR(MP_QSTR_terms), MP_ROM_PTR(&jc_terms_obj) },
    { MP_ROM_QSTR(MP_QSTR_VfsJade), MP_ROM_PTR(&jade_vfs_type) },
    { MP_ROM_QSTR(MP_QSTR_bridge_take), MP_ROM_PTR(&jc_bridge_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_bridge_reply), MP_ROM_PTR(&jc_bridge_reply_obj) },
    { MP_ROM_QSTR(MP_QSTR_bridge_pending), MP_ROM_PTR(&jc_bridge_pending_obj) },
    { MP_ROM_QSTR(MP_QSTR_bridge_call), MP_ROM_PTR(&jc_bridge_call_obj) },
    { MP_ROM_QSTR(MP_QSTR_shell), MP_ROM_PTR(&jc_shell_obj) },
    { MP_ROM_QSTR(MP_QSTR_HEADING), MP_ROM_INT(JIDX_HEADING) },
    { MP_ROM_QSTR(MP_QSTR_LINK), MP_ROM_INT(JIDX_LINK) },
    { MP_ROM_QSTR(MP_QSTR_TAG), MP_ROM_INT(JIDX_TAG) },
    { MP_ROM_QSTR(MP_QSTR_TASK), MP_ROM_INT(JIDX_TASK) },
    { MP_ROM_QSTR(MP_QSTR_META), MP_ROM_INT(JIDX_META) },
    #ifdef ESP_PLATFORM
    { MP_ROM_QSTR(MP_QSTR_platform), MP_ROM_QSTR(MP_QSTR_esp32) },
    #else
    { MP_ROM_QSTR(MP_QSTR_platform), MP_ROM_QSTR(MP_QSTR_host) },
    #endif
};
static MP_DEFINE_CONST_DICT(jadecore_globals, jadecore_globals_table);

const mp_obj_module_t jadecore_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&jadecore_globals,
};

MP_REGISTER_MODULE(MP_QSTR_jadecore, jadecore_module);
