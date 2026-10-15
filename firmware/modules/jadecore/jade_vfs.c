// VfsJade: a MicroPython filesystem object over plain POSIX calls.
//
// On the ESP32 the LittleFS partition belongs to ESP-IDF (mounted at /root by BreezyBox), so the shell,
// the C indexer and Python all see the same files. MicroPython can't mount that partition a second
// time with its own LittleFS driver without corrupting it, so instead VfsJade forwards every call to
// ESP-IDF's POSIX layer. Mounted at "/", Python's /root/... is the shell's /root/...
//
// It keeps its own current directory and always passes absolute, normalised paths down, because
// BreezyBox wraps open/stat/... to resolve relative paths against the *shell's* cwd, and ESP-IDF's
// VFS doesn't understand "." or "..".
//
// VfsJade(prefix="") — prefix is put in front of every path. The firmware uses "" (the real root); the
// unix-port tests use a temporary directory, so "/root/vault" maps to "<tmp>/root/vault" there.

#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/mphal.h"
#include "py/stream.h"
#include "py/builtin.h"
#include "py/mpthread.h"
#include "extmod/vfs.h"

#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "jade_vfs.h"

#define JV_PATH_MAX 256

typedef struct {
    mp_obj_base_t base;
    char prefix[96];
    size_t prefix_len;
    char cwd[JV_PATH_MAX];   // always absolute and normalised, "/" or "/a/b"
    bool readonly;
} jade_vfs_t;

// the prefix of the most recent VfsJade, so jadecore.search() & co. can map vault paths to real ones
char jade_vfs_prefix[96] = "";

// Weak hook for statvfs: total and used bytes of the filesystem holding `path`.
// The ESP32 board answers from esp_littlefs_info(); the default reports nothing.
__attribute__((weak)) int jade_platform_fs_usage(const char *path, size_t *total, size_t *used) {
    (void)path;
    *total = 0;
    *used = 0;
    return -1;
}

// Join `path` onto `cwd`, resolving "." and "..", into out (absolute). Returns false if too long.
bool jade_vfs_normalise(const char *cwd, const char *path, char *out, size_t cap) {
    char buf[JV_PATH_MAX * 2];
    size_t n;
    if (path[0] == '/') {
        n = strlen(path);
        if (n >= sizeof(buf)) {
            return false;
        }
        memcpy(buf, path, n + 1);
    } else {
        size_t cl = strlen(cwd), pl = strlen(path);
        if (cl + 1 + pl >= sizeof(buf)) {
            return false;
        }
        memcpy(buf, cwd, cl);
        buf[cl] = '/';
        memcpy(buf + cl + 1, path, pl + 1);
    }
    // walk components
    size_t o = 0;
    const char *p = buf;
    while (*p) {
        while (*p == '/') {
            p++;
        }
        if (!*p) {
            break;
        }
        const char *e = p;
        while (*e && *e != '/') {
            e++;
        }
        size_t len = (size_t)(e - p);
        if (len == 1 && p[0] == '.') {
            // skip
        } else if (len == 2 && p[0] == '.' && p[1] == '.') {
            while (o > 0 && out[o - 1] != '/') {
                o--;
            }
            if (o > 0) {
                o--;   // drop the slash too
            }
        } else {
            if (o + 1 + len >= cap) {
                return false;
            }
            out[o++] = '/';
            memcpy(out + o, p, len);
            o += len;
        }
        p = e;
    }
    if (o == 0) {
        if (cap < 2) {
            return false;
        }
        out[o++] = '/';
    }
    out[o] = '\0';
    return true;
}

// absolute path as the OS sees it: prefix + normalised path
static const char *real_path(jade_vfs_t *self, mp_obj_t path_in, char *out, size_t cap) {
    const char *path = mp_obj_str_get_str(path_in);
    if (cap <= self->prefix_len + 1 || !jade_vfs_normalise(self->cwd, path, out + self->prefix_len, cap - self->prefix_len)) {
        mp_raise_OSError(ENAMETOOLONG);
    }
    memcpy(out, self->prefix, self->prefix_len);
    if (self->prefix_len && out[self->prefix_len] == '/' && out[self->prefix_len + 1] == '\0') {
        out[self->prefix_len] = '\0';   // "<prefix>/" → "<prefix>"
    }
    return out;
}

static void check_writable(jade_vfs_t *self) {
    if (self->readonly) {
        mp_raise_OSError(MP_EROFS);
    }
}

// ---------------------------------------------------------------- files
typedef struct {
    mp_obj_base_t base;
    int fd;
} jade_file_t;

static const mp_obj_type_t jade_fileio_type;
static const mp_obj_type_t jade_textio_type;

static void file_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    (void)kind;
    jade_file_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "<io.%s %d>", mp_obj_get_type_str(self_in), self->fd);
}

static void check_open(jade_file_t *f) {
    if (f->fd < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("I/O operation on closed file"));
    }
}

static mp_uint_t file_read(mp_obj_t o_in, void *buf, mp_uint_t size, int *errcode) {
    jade_file_t *o = MP_OBJ_TO_PTR(o_in);
    check_open(o);
    MP_THREAD_GIL_EXIT();
    ssize_t r = read(o->fd, buf, size);
    MP_THREAD_GIL_ENTER();
    if (r < 0) {
        *errcode = errno;
        return MP_STREAM_ERROR;
    }
    return (mp_uint_t)r;
}

static mp_uint_t file_write(mp_obj_t o_in, const void *buf, mp_uint_t size, int *errcode) {
    jade_file_t *o = MP_OBJ_TO_PTR(o_in);
    check_open(o);
    MP_THREAD_GIL_EXIT();
    ssize_t r = write(o->fd, buf, size);
    MP_THREAD_GIL_ENTER();
    if (r < 0) {
        *errcode = errno;
        return MP_STREAM_ERROR;
    }
    return (mp_uint_t)r;
}

static mp_uint_t file_ioctl(mp_obj_t o_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    jade_file_t *o = MP_OBJ_TO_PTR(o_in);
    switch (request) {
        case MP_STREAM_FLUSH: {
            check_open(o);
            MP_THREAD_GIL_EXIT();
            int r = fsync(o->fd);
            MP_THREAD_GIL_ENTER();
            if (r < 0 && errno != EINVAL && errno != ENOSYS) {
                *errcode = errno;
                return MP_STREAM_ERROR;
            }
            return 0;
        }
        case MP_STREAM_SEEK: {
            check_open(o);
            struct mp_stream_seek_t *s = (struct mp_stream_seek_t *)arg;
            MP_THREAD_GIL_EXIT();
            off_t off = lseek(o->fd, s->offset, s->whence);
            MP_THREAD_GIL_ENTER();
            if (off == (off_t)-1) {
                *errcode = errno;
                return MP_STREAM_ERROR;
            }
            s->offset = off;
            return 0;
        }
        case MP_STREAM_CLOSE:
            if (o->fd >= 0) {
                MP_THREAD_GIL_EXIT();
                close(o->fd);
                MP_THREAD_GIL_ENTER();
            }
            o->fd = -1;
            return 0;
        case MP_STREAM_GET_FILENO:
            check_open(o);
            return o->fd;
        default:
            *errcode = MP_EINVAL;
            return MP_STREAM_ERROR;
    }
}

static const mp_rom_map_elem_t file_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mp_stream_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&mp_stream_readinto_obj) },
    { MP_ROM_QSTR(MP_QSTR_readline), MP_ROM_PTR(&mp_stream_unbuffered_readline_obj) },
    { MP_ROM_QSTR(MP_QSTR_readlines), MP_ROM_PTR(&mp_stream_unbuffered_readlines_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mp_stream_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_seek), MP_ROM_PTR(&mp_stream_seek_obj) },
    { MP_ROM_QSTR(MP_QSTR_tell), MP_ROM_PTR(&mp_stream_tell_obj) },
    { MP_ROM_QSTR(MP_QSTR_flush), MP_ROM_PTR(&mp_stream_flush_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&mp_stream_close_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&mp_stream_close_obj) },
    { MP_ROM_QSTR(MP_QSTR___enter__), MP_ROM_PTR(&mp_identity_obj) },
    { MP_ROM_QSTR(MP_QSTR___exit__), MP_ROM_PTR(&mp_stream___exit___obj) },
};
static MP_DEFINE_CONST_DICT(file_locals_dict, file_locals_table);

static const mp_stream_p_t fileio_stream_p = {
    .read = file_read,
    .write = file_write,
    .ioctl = file_ioctl,
};
static MP_DEFINE_CONST_OBJ_TYPE(
    jade_fileio_type,
    MP_QSTR_FileIO,
    MP_TYPE_FLAG_ITER_IS_STREAM,
    print, file_print,
    protocol, &fileio_stream_p,
    locals_dict, &file_locals_dict
    );

static const mp_stream_p_t textio_stream_p = {
    .read = file_read,
    .write = file_write,
    .ioctl = file_ioctl,
    .is_text = true,
};
static MP_DEFINE_CONST_OBJ_TYPE(
    jade_textio_type,
    MP_QSTR_TextIOWrapper,
    MP_TYPE_FLAG_ITER_IS_STREAM,
    print, file_print,
    protocol, &textio_stream_p,
    locals_dict, &file_locals_dict
    );

// ---------------------------------------------------------------- the filesystem object
static mp_obj_t vfs_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 1, false);
    jade_vfs_t *self = mp_obj_malloc(jade_vfs_t, type);
    const char *prefix = n_args ? mp_obj_str_get_str(args[0]) : "";
    size_t pl = strlen(prefix);
    while (pl > 0 && prefix[pl - 1] == '/') {
        pl--;
    }
    if (pl >= sizeof(self->prefix)) {
        mp_raise_ValueError(MP_ERROR_TEXT("prefix too long"));
    }
    memcpy(self->prefix, prefix, pl);
    self->prefix[pl] = '\0';
    self->prefix_len = pl;
    strcpy(self->cwd, "/");
    self->readonly = false;
    memcpy(jade_vfs_prefix, self->prefix, pl + 1);
    return MP_OBJ_FROM_PTR(self);
}

static mp_obj_t vfs_mount(mp_obj_t self_in, mp_obj_t readonly, mp_obj_t mkfs) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    if (mp_obj_is_true(mkfs)) {
        mp_raise_OSError(MP_EPERM);
    }
    self->readonly = mp_obj_is_true(readonly);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(vfs_mount_obj, vfs_mount);

static mp_obj_t vfs_umount(mp_obj_t self_in) {
    (void)self_in;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(vfs_umount_obj, vfs_umount);

static mp_obj_t vfs_open(mp_obj_t self_in, mp_obj_t path_in, mp_obj_t mode_in) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    const char *mode = mp_obj_str_get_str(mode_in);
    int rw = O_RDONLY, x = 0;
    const mp_obj_type_t *type = &jade_textio_type;
    for (const char *m = mode; *m; m++) {
        switch (*m) {
            case 'r':
                rw = O_RDONLY;
                break;
            case 'w':
                rw = O_WRONLY;
                x = O_CREAT | O_TRUNC;
                break;
            case 'a':
                rw = O_WRONLY;
                x = O_CREAT | O_APPEND;
                break;
            case 'x':
                rw = O_WRONLY;
                x = O_CREAT | O_EXCL;
                break;
            case '+':
                rw = O_RDWR;
                break;
            case 'b':
                type = &jade_fileio_type;
                break;
            case 't':
                type = &jade_textio_type;
                break;
        }
    }
    if (rw != O_RDONLY) {
        check_writable(self);
    }
    char path[JV_PATH_MAX + 96];
    real_path(self, path_in, path, sizeof(path));
    jade_file_t *f = mp_obj_malloc_with_finaliser(jade_file_t, type);
    f->fd = -1;
    MP_THREAD_GIL_EXIT();
    int fd = open(path, rw | x, 0644);
    MP_THREAD_GIL_ENTER();
    if (fd < 0) {
        mp_raise_OSError(errno);
    }
    f->fd = fd;
    return MP_OBJ_FROM_PTR(f);
}
static MP_DEFINE_CONST_FUN_OBJ_3(vfs_open_obj, vfs_open);

static int stat_path(const char *path, struct stat *st) {
    MP_THREAD_GIL_EXIT();
    int r = stat(path, st);
    MP_THREAD_GIL_ENTER();
    return r;
}

static mp_obj_t vfs_chdir(mp_obj_t self_in, mp_obj_t path_in) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    char norm[JV_PATH_MAX];
    if (!jade_vfs_normalise(self->cwd, mp_obj_str_get_str(path_in), norm, sizeof(norm))) {
        mp_raise_OSError(ENAMETOOLONG);
    }
    if (strcmp(norm, "/") != 0) {
        char path[JV_PATH_MAX + 96];
        struct stat st;
        real_path(self, path_in, path, sizeof(path));
        if (stat_path(path, &st) != 0) {
            mp_raise_OSError(errno);
        }
        if (!S_ISDIR(st.st_mode)) {
            mp_raise_OSError(MP_ENOTDIR);
        }
    }
    strcpy(self->cwd, norm);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(vfs_chdir_obj, vfs_chdir);

static mp_obj_t vfs_getcwd(mp_obj_t self_in) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_str_from_cstr(self->cwd);
}
static MP_DEFINE_CONST_FUN_OBJ_1(vfs_getcwd_obj, vfs_getcwd);

typedef struct {
    mp_obj_base_t base;
    mp_fun_1_t iternext;
    mp_fun_1_t finaliser;
    bool is_str;
    DIR *dir;
    char dirpath[JV_PATH_MAX + 96];   // for stat() when the OS gives no d_type
} listdir_it_t;

static mp_obj_t listdir_next(mp_obj_t self_in) {
    listdir_it_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->dir) {
        return MP_OBJ_STOP_ITERATION;
    }
    for (;;) {
        MP_THREAD_GIL_EXIT();
        struct dirent *d = readdir(self->dir);
        if (!d) {
            closedir(self->dir);
        }
        MP_THREAD_GIL_ENTER();
        if (!d) {
            self->dir = NULL;
            return MP_OBJ_STOP_ITERATION;
        }
        const char *fn = d->d_name;
        if (fn[0] == '.' && (fn[1] == 0 || (fn[1] == '.' && fn[2] == 0))) {
            continue;
        }
        mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(3, NULL));
        t->items[0] = self->is_str ? mp_obj_new_str_from_cstr(fn) : mp_obj_new_bytes((const byte *)fn, strlen(fn));
        mp_int_t type = 0;
        #ifdef DT_DIR
        if (d->d_type == DT_DIR) {
            type = MP_S_IFDIR;
        } else if (d->d_type == DT_REG) {
            type = MP_S_IFREG;
        }
        #endif
        if (!type) {
            char p[JV_PATH_MAX + 96 + 64];
            struct stat st;
            snprintf(p, sizeof(p), "%s/%s", self->dirpath, fn);
            if (stat_path(p, &st) == 0) {
                type = S_ISDIR(st.st_mode) ? MP_S_IFDIR : MP_S_IFREG;
            }
        }
        t->items[1] = MP_OBJ_NEW_SMALL_INT(type);
        t->items[2] = MP_OBJ_NEW_SMALL_INT(0);
        return MP_OBJ_FROM_PTR(t);
    }
}

static mp_obj_t listdir_del(mp_obj_t self_in) {
    listdir_it_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->dir) {
        MP_THREAD_GIL_EXIT();
        closedir(self->dir);
        MP_THREAD_GIL_ENTER();
        self->dir = NULL;
    }
    return mp_const_none;
}

static mp_obj_t vfs_ilistdir(mp_obj_t self_in, mp_obj_t path_in) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    listdir_it_t *it = mp_obj_malloc_with_finaliser(listdir_it_t, &mp_type_polymorph_iter_with_finaliser);
    it->iternext = listdir_next;
    it->finaliser = listdir_del;
    it->is_str = mp_obj_get_type(path_in) == &mp_type_str;
    it->dir = NULL;
    real_path(self, path_in, it->dirpath, sizeof(it->dirpath));
    const char *p = it->dirpath[0] ? it->dirpath : "/";
    MP_THREAD_GIL_EXIT();
    it->dir = opendir(p);
    MP_THREAD_GIL_ENTER();
    if (!it->dir) {
        mp_raise_OSError(errno);
    }
    if (strcmp(it->dirpath, "/") == 0) {
        it->dirpath[0] = '\0';   // so entries are joined as "/name"
    }
    return MP_OBJ_FROM_PTR(it);
}
static MP_DEFINE_CONST_FUN_OBJ_2(vfs_ilistdir_obj, vfs_ilistdir);

typedef int (*path_fn)(const char *);
static mp_obj_t path_call(mp_obj_t self_in, mp_obj_t path_in, path_fn fn) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    check_writable(self);
    char path[JV_PATH_MAX + 96];
    real_path(self, path_in, path, sizeof(path));
    MP_THREAD_GIL_EXIT();
    int r = fn(path);
    MP_THREAD_GIL_ENTER();
    if (r != 0) {
        mp_raise_OSError(errno);
    }
    return mp_const_none;
}

static int do_mkdir(const char *p) {
    return mkdir(p, 0777);
}
static mp_obj_t vfs_mkdir(mp_obj_t self_in, mp_obj_t path_in) {
    return path_call(self_in, path_in, do_mkdir);
}
static MP_DEFINE_CONST_FUN_OBJ_2(vfs_mkdir_obj, vfs_mkdir);

static mp_obj_t vfs_remove(mp_obj_t self_in, mp_obj_t path_in) {
    return path_call(self_in, path_in, unlink);
}
static MP_DEFINE_CONST_FUN_OBJ_2(vfs_remove_obj, vfs_remove);

static mp_obj_t vfs_rmdir(mp_obj_t self_in, mp_obj_t path_in) {
    return path_call(self_in, path_in, rmdir);
}
static MP_DEFINE_CONST_FUN_OBJ_2(vfs_rmdir_obj, vfs_rmdir);

static mp_obj_t vfs_rename(mp_obj_t self_in, mp_obj_t old_in, mp_obj_t new_in) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    check_writable(self);
    char a[JV_PATH_MAX + 96], b[JV_PATH_MAX + 96];
    real_path(self, old_in, a, sizeof(a));
    real_path(self, new_in, b, sizeof(b));
    MP_THREAD_GIL_EXIT();
    struct stat st;
    // POSIX rename replaces an existing file; LittleFS on ESP-IDF does too, FAT doesn't: be uniform
    if (stat(b, &st) == 0 && S_ISREG(st.st_mode) && strcmp(a, b) != 0) {
        unlink(b);
    }
    int r = rename(a, b);
    MP_THREAD_GIL_ENTER();
    if (r != 0) {
        mp_raise_OSError(errno);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(vfs_rename_obj, vfs_rename);

static mp_obj_t vfs_stat(mp_obj_t self_in, mp_obj_t path_in) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    char path[JV_PATH_MAX + 96];
    real_path(self, path_in, path, sizeof(path));
    struct stat st;
    mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(10, NULL));
    if (path[0] == '\0' || strcmp(path, "/") == 0) {   // the root always exists (ESP-IDF can't stat it)
        memset(&st, 0, sizeof(st));
        st.st_mode = S_IFDIR | 0755;
    } else if (stat_path(path, &st) != 0) {
        mp_raise_OSError(errno);
    }
    t->items[0] = MP_OBJ_NEW_SMALL_INT(S_ISDIR(st.st_mode) ? MP_S_IFDIR : MP_S_IFREG);
    for (int i = 1; i < 10; i++) {
        t->items[i] = MP_OBJ_NEW_SMALL_INT(0);
    }
    t->items[6] = mp_obj_new_int_from_uint(st.st_size);
    t->items[7] = mp_obj_new_int_from_uint(st.st_atime);
    t->items[8] = mp_obj_new_int_from_uint(st.st_mtime);
    t->items[9] = mp_obj_new_int_from_uint(st.st_ctime);
    return MP_OBJ_FROM_PTR(t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(vfs_stat_obj, vfs_stat);

static mp_obj_t vfs_statvfs(mp_obj_t self_in, mp_obj_t path_in) {
    jade_vfs_t *self = MP_OBJ_TO_PTR(self_in);
    char path[JV_PATH_MAX + 96];
    real_path(self, path_in, path, sizeof(path));
    size_t total = 0, used = 0;
    jade_platform_fs_usage(path, &total, &used);
    const size_t bs = 4096;
    mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(10, NULL));
    t->items[0] = MP_OBJ_NEW_SMALL_INT(bs);
    t->items[1] = MP_OBJ_NEW_SMALL_INT(bs);
    t->items[2] = mp_obj_new_int_from_uint(total / bs);
    t->items[3] = mp_obj_new_int_from_uint((total - used) / bs);
    t->items[4] = mp_obj_new_int_from_uint((total - used) / bs);
    for (int i = 5; i < 9; i++) {
        t->items[i] = MP_OBJ_NEW_SMALL_INT(0);
    }
    t->items[9] = MP_OBJ_NEW_SMALL_INT(255);
    return MP_OBJ_FROM_PTR(t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(vfs_statvfs_obj, vfs_statvfs);

static mp_import_stat_t vfs_import_stat(void *self_in, const char *path) {
    jade_vfs_t *self = self_in;
    char out[JV_PATH_MAX + 96];
    if (self->prefix_len + 1 >= sizeof(out) || !jade_vfs_normalise(self->cwd, path, out + self->prefix_len, sizeof(out) - self->prefix_len)) {
        return MP_IMPORT_STAT_NO_EXIST;
    }
    memcpy(out, self->prefix, self->prefix_len);
    struct stat st;
    if (stat(out, &st) == 0) {
        if (S_ISDIR(st.st_mode)) {
            return MP_IMPORT_STAT_DIR;
        } else if (S_ISREG(st.st_mode)) {
            return MP_IMPORT_STAT_FILE;
        }
    }
    return MP_IMPORT_STAT_NO_EXIST;
}

static const mp_rom_map_elem_t vfs_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_mount), MP_ROM_PTR(&vfs_mount_obj) },
    { MP_ROM_QSTR(MP_QSTR_umount), MP_ROM_PTR(&vfs_umount_obj) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&vfs_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_chdir), MP_ROM_PTR(&vfs_chdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_getcwd), MP_ROM_PTR(&vfs_getcwd_obj) },
    { MP_ROM_QSTR(MP_QSTR_ilistdir), MP_ROM_PTR(&vfs_ilistdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_mkdir), MP_ROM_PTR(&vfs_mkdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_remove), MP_ROM_PTR(&vfs_remove_obj) },
    { MP_ROM_QSTR(MP_QSTR_rename), MP_ROM_PTR(&vfs_rename_obj) },
    { MP_ROM_QSTR(MP_QSTR_rmdir), MP_ROM_PTR(&vfs_rmdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_stat), MP_ROM_PTR(&vfs_stat_obj) },
    { MP_ROM_QSTR(MP_QSTR_statvfs), MP_ROM_PTR(&vfs_statvfs_obj) },
};
static MP_DEFINE_CONST_DICT(vfs_locals_dict, vfs_locals_table);

static const mp_vfs_proto_t vfs_proto = {
    .import_stat = vfs_import_stat,
};

MP_DEFINE_CONST_OBJ_TYPE(
    jade_vfs_type,
    MP_QSTR_VfsJade,
    MP_TYPE_FLAG_NONE,
    make_new, vfs_make_new,
    protocol, &vfs_proto,
    locals_dict, &vfs_locals_dict
    );
