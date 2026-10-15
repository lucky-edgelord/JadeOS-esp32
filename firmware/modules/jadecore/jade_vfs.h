#pragma once
#include <stdbool.h>
#include <stddef.h>

extern char jade_vfs_prefix[96];
bool jade_vfs_normalise(const char *cwd, const char *path, char *out, size_t cap);
int jade_platform_fs_usage(const char *path, size_t *total, size_t *used);
