// JadeOS vault indexer and Markdown scanner. See jade_idx.h.
#include "jade_idx.h"

#include <dirent.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

// Called every few files so a long search doesn't starve other tasks (the ESP32 board yields to
// FreeRTOS here). The default does nothing.
__attribute__((weak)) void jidx_yield_hook(void) {
}

static inline char lc(char c) {
    return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
}
static inline int is_word(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || (unsigned char)c >= 0x80;
}
static inline int is_space(char c) {
    return c == ' ' || c == '\t';
}

// ---------------------------------------------------------------- line reader
typedef struct {
    int fd;
    char buf[1024];
    int pos, len;
    int eof;
    char line[JIDX_LINE_MAX + 1];
    int line_len;
    int lineno;     // 1-based number of the line currently in `line`
    int partial;    // the line was longer than JIDX_LINE_MAX; this is one chunk of it
    int cont;       // this chunk continues the previous line (same lineno)
    long bytes;
} reader_t;

static int reader_open(reader_t *r, const char *path) {
    r->fd = open(path, O_RDONLY, 0);
    r->pos = r->len = 0;
    r->eof = 0;
    r->lineno = 0;
    r->partial = 0;
    r->cont = 0;
    r->bytes = 0;
    return r->fd < 0 ? -1 : 0;
}
static void reader_close(reader_t *r) {
    if (r->fd >= 0) {
        close(r->fd);
    }
    r->fd = -1;
}
// Next line (without the newline) into r->line. Returns 0 at end of file.
static int reader_next(reader_t *r) {
    r->cont = r->partial;
    if (!r->partial) {
        r->lineno++;
    }
    r->partial = 0;
    r->line_len = 0;
    for (;;) {
        if (r->pos >= r->len) {
            if (r->eof) {
                break;
            }
            ssize_t n = read(r->fd, r->buf, sizeof(r->buf));
            if (n <= 0) {
                r->eof = 1;
                break;
            }
            r->bytes += n;
            r->pos = 0;
            r->len = (int)n;
        }
        char c = r->buf[r->pos++];
        if (c == '\n') {
            goto done;
        }
        if (r->line_len >= JIDX_LINE_MAX) {
            r->pos--;           // keep this byte for the next chunk
            r->partial = 1;
            goto done;
        }
        r->line[r->line_len++] = c;
    }
    if (r->line_len == 0 && r->eof) {
        return 0;
    }
done:
    if (r->line_len > 0 && r->line[r->line_len - 1] == '\r') {
        r->line_len--;
    }
    r->line[r->line_len] = '\0';
    return 1;
}

// ---------------------------------------------------------------- query terms
int jidx_terms(const char *q, char terms[][JIDX_TERM_MAX + 1], int max_terms) {
    int n = 0;
    if (max_terms > JIDX_MAX_TERMS) {
        max_terms = JIDX_MAX_TERMS;
    }
    while (*q && n < max_terms) {
        while (*q && !is_word(*q) && *q != '#') {
            q++;
        }
        if (!*q) {
            break;
        }
        int len = 0;
        char t[JIDX_TERM_MAX + 1];
        if (*q == '#') {
            t[len++] = '#';
            q++;
        }
        while (*q && (is_word(*q) || (len > 0 && t[0] == '#' && *q == '/'))) {
            if (len < JIDX_TERM_MAX) {
                t[len++] = lc(*q);
            }
            q++;
        }
        t[len] = '\0';
        if (len < 2 || (t[0] == '#' && len < 2)) {
            continue;
        }
        int dup = 0;
        for (int i = 0; i < n; i++) {
            if (strcmp(terms[i], t) == 0) {
                dup = 1;
            }
        }
        if (!dup) {
            memcpy(terms[n++], t, (size_t)len + 1);
        }
    }
    return n;
}

// occurrences of term in a lowercased line (at most `cap`); a "#tag" term must end at a word boundary
static int count_term(const char *low, const char *term, int cap) {
    int n = 0;
    size_t tl = strlen(term);
    const char *p = low;
    while (n < cap && (p = strstr(p, term)) != NULL) {
        if (term[0] == '#') {
            char after = p[tl];
            char before = p > low ? p[-1] : ' ';
            if (!is_word(after) && after != '/' && !is_word(before)) {
                n++;
            }
        } else {
            n++;
        }
        p += tl;
    }
    return n;
}

// whole-word match of a bare tag in a frontmatter "tags:" value
static int count_tag_word(const char *low, const char *tag) {
    size_t tl = strlen(tag);
    for (const char *p = low; (p = strstr(p, tag)) != NULL; p += tl) {
        char before = p > low ? p[-1] : ' ';
        if (!is_word(before) && before != '/' && !is_word(p[tl]) && p[tl] != '/') {
            return 1;
        }
    }
    return 0;
}

// the visible text of a line: leading Markdown markers and spaces removed
static const char *line_text(const char *s, size_t *len) {
    const char *e = s + *len;
    while (s < e && is_space(*s)) {
        s++;
    }
    while (s < e && (*s == '#' || *s == '>')) {
        s++;
    }
    while (s < e && is_space(*s)) {
        s++;
    }
    if (s + 1 < e && (*s == '-' || *s == '*' || *s == '+') && is_space(s[1])) {
        s += 2;
    }
    if (s + 3 <= e && s[0] == '[' && (s[1] == ' ' || s[1] == 'x' || s[1] == 'X') && s[2] == ']') {
        s += 3;
    }
    while (s < e && is_space(*s)) {
        s++;
    }
    while (e > s && is_space(e[-1])) {
        e--;
    }
    *len = (size_t)(e - s);
    return s;
}

// cut at <= cap bytes without splitting a UTF-8 character
static size_t utf8_cut(const char *s, size_t len, size_t cap) {
    if (len <= cap) {
        return len;
    }
    size_t n = cap;
    while (n > 0 && ((unsigned char)s[n] & 0xC0) == 0x80) {
        n--;
    }
    return n;
}

static int ends_md(const char *name) {
    size_t n = strlen(name);
    if (n >= 3 && lc(name[n - 3]) == '.' && lc(name[n - 2]) == 'm' && lc(name[n - 1]) == 'd') {
        return 1;
    }
    return n >= 9 && strcmp(name + n - 9, ".markdown") == 0;
}

typedef struct {
    char terms[JIDX_MAX_TERMS][JIDX_TERM_MAX + 1];
    int nterms;
    jidx_hit_t *hits;
    int max_hits, nhits;
    int snip_len;
    jidx_stats_t *stats;
    reader_t rd;                       // one reader, reused for every file
    char low[JIDX_LINE_MAX + 1];
} search_t;

static void add_hit(search_t *s, const char *path, int score, int line, uint16_t mask, const char *snip, size_t snip_len) {
    int pos = s->nhits;
    if (pos == s->max_hits) {
        if (score <= s->hits[pos - 1].score) {
            return;
        }
        pos--;  // drop the worst
    } else {
        s->nhits++;
    }
    while (pos > 0 && s->hits[pos - 1].score < score) {
        s->hits[pos] = s->hits[pos - 1];
        pos--;
    }
    jidx_hit_t *h = &s->hits[pos];
    strncpy(h->path, path, JIDX_PATH_MAX - 1);
    h->path[JIDX_PATH_MAX - 1] = '\0';
    h->score = score;
    h->line = line;
    h->terms_hit = mask;
    memcpy(h->snippet, snip, snip_len);
    h->snippet[snip_len] = '\0';
}

static void search_file(search_t *s, const char *path) {
    reader_t *r = &s->rd;
    if (reader_open(r, path) != 0) {
        return;
    }
    // the note's name counts a lot
    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;
    char name[96];
    size_t bl = strlen(base);
    if (bl >= sizeof(name)) {
        bl = sizeof(name) - 1;
    }
    for (size_t i = 0; i < bl; i++) {
        name[i] = lc(base[i]);
    }
    name[bl] = '\0';
    char *dot = strrchr(name, '.');
    if (dot) {
        *dot = '\0';
    }
    int score = 0;
    uint16_t mask = 0;
    for (int t = 0; t < s->nterms; t++) {
        if (s->terms[t][0] != '#' && strstr(name, s->terms[t])) {   // tags never match names
            score += 12;
            mask |= (uint16_t)(1u << t);
        }
    }
    int best = 0, best_line = 0;
    char snip[JIDX_SNIP_MAX];
    size_t snip_n = 0;
    int in_meta = 0;
    while (reader_next(r)) {
        if (r->lineno == 1 && !r->cont && strcmp(r->line, "---") == 0) {
            in_meta = 1;
            continue;
        }
        if (in_meta && !r->cont && strcmp(r->line, "---") == 0) {
            in_meta = 0;
            continue;
        }
        for (int i = 0; i <= r->line_len; i++) {
            s->low[i] = lc(r->line[i]);
        }
        int heading = !r->cont && r->line[0] == '#' && (r->line[1] == ' ' || r->line[1] == '#');
        int ls = 0, distinct = 0;
        for (int t = 0; t < s->nterms; t++) {
            int c = count_term(s->low, s->terms[t], 3);
            if (!c && s->terms[t][0] == '#' && in_meta && strncmp(s->low, "tags:", 5) == 0) {
                c = count_tag_word(s->low + 5, s->terms[t] + 1);   // tags: [a, b] in frontmatter
            }
            if (c) {
                ls += c * (heading ? 4 : 1);
                distinct++;
                mask |= (uint16_t)(1u << t);
            }
        }
        if (!ls) {
            continue;
        }
        ls += (distinct - 1) * 3;
        if (ls > 24) {
            ls = 24;
        }
        score += ls;
        if (ls > best) {
            best = ls;
            best_line = r->lineno;
            size_t n = (size_t)r->line_len;
            const char *txt = line_text(r->line, &n);
            char plain[JIDX_LINE_MAX + 1];
            n = jidx_strip(txt, n, plain, sizeof(plain) - 1);   // the snippet is plain text
            snip_n = utf8_cut(plain, n, (size_t)s->snip_len);
            memcpy(snip, plain, snip_n);
        }
    }
    if (s->stats) {
        s->stats->files++;
        s->stats->bytes += (int32_t)r->bytes;
    }
    reader_close(r);
    if (!mask) {
        return;
    }
    int coverage = 0;
    for (int t = 0; t < s->nterms; t++) {
        coverage += (mask >> t) & 1;
    }
    score += coverage * 8 + (coverage == s->nterms ? 10 : 0);
    if (s->stats) {
        s->stats->matched++;
    }
    add_hit(s, path, score, best_line, mask, snip, snip_n);
}

int jidx_search(const char *root, const char *query, jidx_hit_t *hits, int max_hits, int snip_len, jidx_stats_t *stats) {
    static search_t s;  // ~3 KB; not on the (small) task stack. The caller holds the GIL or a lock.
    memset(&s.terms, 0, sizeof(s.terms));
    s.nterms = jidx_terms(query, s.terms, JIDX_MAX_TERMS);
    s.hits = hits;
    s.max_hits = max_hits;
    s.nhits = 0;
    s.snip_len = snip_len <= 0 ? 0 : snip_len >= JIDX_SNIP_MAX ? JIDX_SNIP_MAX - 1 : snip_len;
    s.stats = stats;
    if (stats) {
        memset(stats, 0, sizeof(*stats));
    }
    if (s.nterms == 0 || max_hits <= 0) {
        return 0;
    }
    char path[JIDX_PATH_MAX];
    size_t rl = strlen(root);
    if (rl >= sizeof(path) - 2) {
        return -1;
    }
    memcpy(path, root, rl + 1);
    while (rl > 1 && path[rl - 1] == '/') {
        path[--rl] = '\0';
    }
    DIR *stack[JIDX_MAX_DEPTH];
    size_t lens[JIDX_MAX_DEPTH];
    int depth = 0;
    stack[0] = opendir(path);
    if (!stack[0]) {
        return -1;
    }
    lens[0] = rl;
    int since_yield = 0;
    while (depth >= 0) {
        struct dirent *e = readdir(stack[depth]);
        if (!e) {
            closedir(stack[depth]);
            depth--;
            continue;
        }
        if (e->d_name[0] == '.') {
            continue;
        }
        size_t base = lens[depth];
        size_t nl = strlen(e->d_name);
        if (base + 1 + nl >= sizeof(path)) {
            continue;
        }
        path[base] = '/';
        memcpy(path + base + 1, e->d_name, nl + 1);
        if (base == 1 && path[0] == '/') {    // root is "/": avoid "//name"
            memmove(path + 1, path + 2, nl + 1);
        }
        struct stat st;
        if (stat(path, &st) != 0) {
            path[base] = '\0';
            continue;
        }
        if (S_ISDIR(st.st_mode)) {
            if (depth + 1 < JIDX_MAX_DEPTH) {
                DIR *d = opendir(path);
                if (d) {
                    depth++;
                    stack[depth] = d;
                    lens[depth] = strlen(path);
                    continue;  // keep `path` as the new directory
                }
            }
        } else if (S_ISREG(st.st_mode) && ends_md(e->d_name)) {
            search_file(&s, path);
            if (++since_yield >= 8) {
                since_yield = 0;
                jidx_yield_hook();
            }
        }
        path[lens[depth]] = '\0';
    }
    return s.nhits;
}

// ---------------------------------------------------------------- structure scan
static int scan_line_inline(const char *l, int len, int kinds, jidx_scan_cb cb, void *ctx, int lineno) {
    int i = 0;
    while (i < len) {
        char c = l[i];
        if (c == '`') {  // skip inline code
            int j = i + 1;
            while (j < len && l[j] != '`') {
                j++;
            }
            i = j + 1;
            continue;
        }
        if ((kinds & JIDX_LINK) && c == '[' && i + 1 < len && l[i + 1] == '[') {
            int j = i + 2, bar = -1;
            while (j + 1 < len && !(l[j] == ']' && l[j + 1] == ']')) {
                if (l[j] == '|' && bar < 0) {
                    bar = j;
                }
                j++;
            }
            if (j + 1 < len) {
                int ts = i + 2, te = bar >= 0 ? bar : j;
                int hash = ts;  // drop #heading
                while (hash < te && l[hash] != '#') {
                    hash++;
                }
                if (hash > ts) {
                    cb(ctx, JIDX_LINK, l + ts, (size_t)(hash - ts), bar >= 0 ? l + bar + 1 : "", bar >= 0 ? (size_t)(j - bar - 1) : 0, i > 0 && l[i - 1] == '!', lineno);
                }
                i = j + 2;
                continue;
            }
        }
        if ((kinds & JIDX_LINK) && c == '[' && (i == 0 || l[i - 1] != '!')) {   // [text](target.md)
            int j = i + 1;
            while (j < len && l[j] != ']') {
                j++;
            }
            if (j + 1 < len && l[j + 1] == '(') {
                int k = j + 2;
                while (k < len && l[k] != ')' && l[k] != ' ') {
                    k++;
                }
                int tl = k - (j + 2);
                const char *t = l + j + 2;
                int is_md = tl > 3 && lc(t[tl - 3]) == '.' && lc(t[tl - 2]) == 'm' && lc(t[tl - 1]) == 'd';
                int has_scheme = 0;
                for (int x = 0; x < tl && x < 12; x++) {
                    if (t[x] == ':') {
                        has_scheme = 1;
                    }
                }
                if (is_md && !has_scheme) {
                    cb(ctx, JIDX_LINK, t, (size_t)tl, l + i + 1, (size_t)(j - i - 1), 0, lineno);
                }
                if (k < len) {
                    i = k + 1;
                    continue;
                }
            }
        }
        if ((kinds & JIDX_TAG) && c == '#' && (i == 0 || is_space(l[i - 1]) || l[i - 1] == '(' || l[i - 1] == ',') && i + 1 < len &&
            ((l[i + 1] >= 'a' && l[i + 1] <= 'z') || (l[i + 1] >= 'A' && l[i + 1] <= 'Z'))) {
            int j = i + 1;
            while (j < len && (is_word(l[j]) || l[j] == '/')) {
                j++;
            }
            int e = j;
            while (e > i + 1 && (l[e - 1] == '/' || l[e - 1] == '-')) {
                e--;
            }
            cb(ctx, JIDX_TAG, l + i + 1, (size_t)(e - i - 1), "", 0, 0, lineno);
            i = j;
            continue;
        }
        i++;
    }
    return 0;
}

int jidx_scan(const char *path, int kinds, jidx_scan_cb cb, void *ctx) {
    static reader_t r;
    if (reader_open(&r, path) != 0) {
        return -1;
    }
    int in_meta = 0, in_fence = 0;
    while (reader_next(&r)) {
        const char *l = r.line;
        int len = r.line_len;
        if (r.cont) {
            if (!in_fence && !in_meta) {
                scan_line_inline(l, len, kinds, cb, ctx, r.lineno);
            }
            continue;
        }
        if (r.lineno == 1 && strcmp(l, "---") == 0) {
            in_meta = 1;
            continue;
        }
        if (in_meta) {
            if (strcmp(l, "---") == 0 || strcmp(l, "...") == 0) {
                in_meta = 0;
                continue;
            }
            if (kinds & JIDX_META) {
                const char *colon = memchr(l, ':', (size_t)len);
                if (colon && colon > l && !is_space(l[0])) {
                    const char *v = colon + 1;
                    while (v < l + len && is_space(*v)) {
                        v++;
                    }
                    const char *ke = colon;
                    while (ke > l && is_space(ke[-1])) {
                        ke--;
                    }
                    cb(ctx, JIDX_META, l, (size_t)(ke - l), v, (size_t)(l + len - v), 0, r.lineno);
                }
            }
            continue;
        }
        int s = 0;
        while (s < len && is_space(l[s])) {
            s++;
        }
        if ((l[s] == '`' && l[s + 1] == '`' && l[s + 2] == '`') || (l[s] == '~' && l[s + 1] == '~' && l[s + 2] == '~')) {
            in_fence = !in_fence;
            continue;
        }
        if (in_fence) {
            continue;
        }
        if (l[0] == '#') {
            int lvl = 0;
            while (lvl < len && l[lvl] == '#') {
                lvl++;
            }
            if (lvl <= 6 && lvl < len && is_space(l[lvl])) {
                if (kinds & JIDX_HEADING) {
                    size_t n = (size_t)len;
                    const char *t = line_text(l, &n);
                    cb(ctx, JIDX_HEADING, t, n, "", 0, lvl, r.lineno);
                }
                scan_line_inline(l + lvl, len - lvl, kinds & ~JIDX_TAG, cb, ctx, r.lineno);   // '#' here is not a tag
                continue;
            }
        }
        if ((kinds & JIDX_TASK) && s + 5 < len + 1 && (l[s] == '-' || l[s] == '*' || l[s] == '+') && l[s + 1] == ' ' && l[s + 2] == '[' &&
            (l[s + 3] == ' ' || l[s + 3] == 'x' || l[s + 3] == 'X') && l[s + 4] == ']') {
            size_t n = (size_t)len;
            const char *t = line_text(l, &n);
            cb(ctx, JIDX_TASK, t, n, "", 0, l[s + 3] != ' ', r.lineno);
        }
        scan_line_inline(l, len, kinds, cb, ctx, r.lineno);
    }
    reader_close(&r);
    return 0;
}

// ---------------------------------------------------------------- strip to plain text
size_t jidx_strip(const char *in, size_t len, char *out, size_t cap) {
    size_t i = 0, o = 0;
    int blank_run = 0, in_fence = 0, first = 1, in_meta = 0;
    while (i < len) {
        size_t ls = i, le = i;
        while (le < len && in[le] != '\n') {
            le++;
        }
        size_t next = le < len ? le + 1 : le;
        if (le > ls && in[le - 1] == '\r') {
            le--;
        }
        size_t s = ls;
        while (s < le && is_space(in[s])) {
            s++;
        }
        int is_fence = le - s >= 3 && ((in[s] == '`' && in[s + 1] == '`' && in[s + 2] == '`') || (in[s] == '~' && in[s + 1] == '~' && in[s + 2] == '~'));
        int is_meta_edge = (le - ls == 3 && in[ls] == '-' && in[ls + 1] == '-' && in[ls + 2] == '-') && (first || in_meta);
        first = 0;
        if (is_meta_edge) {
            in_meta = !in_meta;
            i = next;
            continue;
        }
        if (is_fence) {
            in_fence = !in_fence;
            i = next;
            continue;
        }
        size_t start_o = o;
        if (in_fence) {   // code is kept as it is
            for (size_t k = ls; k < le && o < cap; k++) {
                out[o++] = in[k];
            }
        } else {
            size_t k = ls;
            // indentation of list items is kept; heading and quote markers go
            while (k < le && is_space(in[k]) && o < cap) {
                out[o++] = in[k++];
            }
            if (k < le && in[k] == '#') {
                size_t h = k;
                while (h < le && in[h] == '#') {
                    h++;
                }
                if (h < le && is_space(in[h])) {
                    k = h + 1;
                }
            }
            while (k < le && in[k] == '>') {
                k++;
                if (k < le && is_space(in[k])) {
                    k++;
                }
            }
            while (k < le && o < cap) {
                char c = in[k];
                if (c == '*' || c == '`') {
                    k++;
                    continue;
                }
                if (c == '!' && k + 1 < le && in[k + 1] == '[' && !(k + 2 < le && in[k + 2] == '[')) {
                    k++;   // image: keep the alt text via the link rule below
                    continue;
                }
                if (c == '[' && k + 1 < le && in[k + 1] == '[') {   // [[target|alias]]
                    size_t j = k + 2, bar = 0;
                    while (j + 1 < le && !(in[j] == ']' && in[j + 1] == ']')) {
                        if (in[j] == '|' && !bar) {
                            bar = j;
                        }
                        j++;
                    }
                    if (j + 1 < le) {
                        size_t a = bar ? bar + 1 : k + 2, b = j;
                        if (!bar) {
                            size_t h = a;
                            while (h < b && in[h] != '#') {
                                h++;
                            }
                            b = h;
                        }
                        for (size_t x = a; x < b && o < cap; x++) {
                            out[o++] = in[x];
                        }
                        k = j + 2;
                        continue;
                    }
                }
                if (c == '[') {   // [text](url)
                    size_t j = k + 1;
                    while (j < le && in[j] != ']') {
                        j++;
                    }
                    if (j + 1 < le && in[j + 1] == '(') {
                        size_t u = j + 2;
                        while (u < le && in[u] != ')') {
                            u++;
                        }
                        if (u < le) {
                            for (size_t x = k + 1; x < j && o < cap; x++) {
                                if (in[x] != '*' && in[x] != '`') {
                                    out[o++] = in[x];
                                }
                            }
                            k = u + 1;
                            continue;
                        }
                    }
                }
                if (c == '<' && k + 1 < le && ((in[k + 1] >= 'a' && in[k + 1] <= 'z') || (in[k + 1] >= 'A' && in[k + 1] <= 'Z') || in[k + 1] == '/' || in[k + 1] == '!')) {
                    size_t j = k + 1;
                    while (j < le && in[j] != '>' && in[j] != '<') {
                        j++;
                    }
                    if (j < le && in[j] == '>') {
                        k = j + 1;
                        continue;
                    }
                }
                out[o++] = c;
                k++;
            }
        }
        while (o > start_o && is_space(out[o - 1])) {
            o--;
        }
        if (o == start_o) {
            if (blank_run || o == 0) {   // no leading blank lines, no runs of them
                i = next;
                continue;
            }
            blank_run = 1;
        } else {
            blank_run = 0;
        }
        if (o < cap) {
            out[o++] = '\n';
        }
        i = next;
    }
    while (o > 0 && (out[o - 1] == '\n' || is_space(out[o - 1]))) {
        o--;
    }
    return o;
}
