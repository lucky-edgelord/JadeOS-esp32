// JadeOS vault indexer and Markdown scanner.
//
// Plain C over POSIX file calls (open/read/close, opendir/readdir, stat), so the same code runs on the
// ESP32-S3 (through ESP-IDF's VFS and LittleFS) and on Linux (host tests). Nothing here allocates: files
// are streamed through a fixed buffer, results go into caller-provided arrays, and callbacks receive
// pointers into the line buffer that are only valid during the call.
#pragma once

#include <stddef.h>
#include <stdint.h>

#define JIDX_PATH_MAX   192   // longest vault path handled
#define JIDX_LINE_MAX   512   // longer lines are split (search) or truncated (callbacks)
#define JIDX_SNIP_MAX   240   // longest snippet kept per hit
#define JIDX_MAX_TERMS  8     // query terms used
#define JIDX_TERM_MAX   32    // longest query term
#define JIDX_MAX_DEPTH  8     // directory depth searched

typedef struct {
    char path[JIDX_PATH_MAX];
    int32_t score;
    int32_t line;                  // 1-based line of the best-matching line
    uint16_t terms_hit;            // bitmask of query terms found anywhere in the note
    char snippet[JIDX_SNIP_MAX];   // the best line, Markdown markers stripped, cut to snip_len
} jidx_hit_t;

typedef struct {
    int32_t files;    // .md files scanned
    int32_t bytes;    // bytes read
    int32_t matched;  // files with at least one term
} jidx_stats_t;

// Search every .md file under `root` (recursively, skipping names that start with '.').
// The query is split into lowercase terms; "#tag" terms keep their '#'. Results are ranked by score,
// best first, and at most max_hits are kept. Returns the number of hits, or -1 if root can't be opened.
int jidx_search(const char *root, const char *query, jidx_hit_t *hits, int max_hits, int snip_len, jidx_stats_t *stats);

// Scan one note line by line and report its structure through `cb`.
enum {
    JIDX_HEADING = 1,   // a = heading text, n = level (1-6)
    JIDX_LINK = 2,      // a = target, b = alias (may be empty), from [[target|alias]] or [text](target.md)
    JIDX_TAG = 4,       // a = tag without '#'
    JIDX_TASK = 8,      // a = task text, n = 1 if done
    JIDX_META = 16,     // a = key, b = value (frontmatter between leading --- lines)
};
typedef void (*jidx_scan_cb)(void *ctx, int kind, const char *a, size_t alen, const char *b, size_t blen, int n, int line);
// Returns 0, or -1 if the file can't be opened. `kinds` is a mask of the kinds wanted.
int jidx_scan(const char *path, int kinds, jidx_scan_cb cb, void *ctx);

// Markdown → plain text for sending to a model: drops heading/emphasis/code markers, turns
// [text](url) into text, [[target|alias]] into alias (or target), images into their alt text, HTML tags
// out, and squeezes runs of blank lines. out may be the same buffer as in. Returns the output length.
size_t jidx_strip(const char *in, size_t len, char *out, size_t cap);

// Lowercase ASCII copy of the query terms; exposed for tests. Returns the number of terms.
int jidx_terms(const char *query, char terms[][JIDX_TERM_MAX + 1], int max_terms);
