// Host test for jade_idx.c, built with AddressSanitizer + UBSan (tests/run_c.sh).
#include "../../firmware/modules/jadecore/jade_idx.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int fails = 0, passes = 0;
#define CHECK(c, ...) do { if (c) { passes++; } else { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static char root[256];
static void put(const char *rel, const char *text) {
    char p[512];
    snprintf(p, sizeof p, "%s/%s", root, rel);
    char d[512];
    strcpy(d, p);
    for (char *s = d + strlen(root) + 1; *s; s++) {
        if (*s == '/') { *s = 0; mkdir(d, 0755); *s = '/'; }
    }
    FILE *f = fopen(p, "w");
    fputs(text, f);
    fclose(f);
}

typedef struct { int n; char log[4096]; } scan_ctx;
static void on_scan(void *c, int kind, const char *a, size_t al, const char *b, size_t bl, int n, int line) {
    scan_ctx *x = c;
    char item[300];
    snprintf(item, sizeof item, "%d:%.*s|%.*s|%d@%d;", kind, (int)al, a, (int)bl, b, n, line);
    strncat(x->log, item, sizeof(x->log) - strlen(x->log) - 1);
    x->n++;
}

int main(void) {
    snprintf(root, sizeof root, "/tmp/jidx-test-%d", (int)getpid());
    mkdir(root, 0755);
    put("Projects/Garden plan.md", "---\ntags: [garden, spring]\nstatus: active\n---\n# Garden plan\nPlant **tomatoes** in May.\n## Watering\nWater the tomatoes every morning. #garden\n- [ ] buy seeds\n- [x] dig beds\nSee [[Seeds|seed list]] and [notes](Other.md).\n");
    put("Seeds.md", "# Seeds\nTomato, basil, chili.\n```\n#notatag [[NotALink]]\n```\nInline `#code [[nope]]` here. Real #tag-one and #nested/tag.\n");
    put("Journal/2026-10-14.md", "Today I thought about nothing in particular.\nLong line: ");
    put(".hidden/secret.md", "tomatoes tomatoes tomatoes");
    put("notes.txt", "tomatoes but not markdown");
    // a very long line (longer than JIDX_LINE_MAX) with the term at the end
    {
        char big[3000];
        memset(big, 'x', sizeof big);
        memcpy(big + 2900, " zebrafish", 10);
        big[2910] = '\n';
        big[2911] = 0;
        put("Long.md", big);
    }
    // deep nesting beyond JIDX_MAX_DEPTH
    put("a/b/c/d/e/f/g/h/i/j/Deep.md", "tomatoes deep");

    jidx_hit_t hits[5];
    jidx_stats_t st;
    int n = jidx_search(root, "Tomatoes watering", hits, 5, 80, &st);
    CHECK(n >= 1, "expected hits, got %d", n);
    CHECK(n >= 1 && strstr(hits[0].path, "Garden plan.md"), "best hit should be Garden plan, got %s", n ? hits[0].path : "-");
    CHECK(n >= 1 && hits[0].line == 7, "best line should be the '## Watering' heading (7), got %d", n ? hits[0].line : -1);
    CHECK(n >= 1 && strcmp(hits[0].snippet, "Watering") == 0, "snippet should be 'Watering', got '%s'", n ? hits[0].snippet : "");
    CHECK(n >= 1 && hits[0].terms_hit == 3, "both terms found, mask %d", n ? hits[0].terms_hit : -1);
    for (int i = 0; i < n; i++) {
        CHECK(!strstr(hits[i].path, ".hidden") && !strstr(hits[i].path, "notes.txt") && !strstr(hits[i].path, "Deep.md"), "hidden, non-md and too-deep files are skipped: %s", hits[i].path);
        if (i) CHECK(hits[i - 1].score >= hits[i].score, "sorted by score");
    }
    CHECK(st.files == 4, "4 .md files scanned (not hidden / too deep), got %d", st.files);

    n = jidx_search(root, "zebrafish", hits, 5, 40, &st);
    CHECK(n == 1 && strstr(hits[0].path, "Long.md") && hits[0].line == 1, "a term past the line buffer is still found (line 1), n=%d line=%d", n, n ? hits[0].line : -1);
    CHECK(n == 1 && strlen(hits[0].snippet) <= 40, "snippet cut to snip_len");

    n = jidx_search(root, "#garden", hits, 5, 80, &st);
    CHECK(n == 1 && strstr(hits[0].path, "Garden plan.md"), "#tag search finds inline and frontmatter tags, n=%d", n);
    n = jidx_search(root, "#gard", hits, 5, 80, &st);
    CHECK(n == 0, "#tag search needs the whole tag, n=%d", n);
    n = jidx_search(root, "seeds", hits, 1, 80, &st);
    CHECK(n == 1 && strstr(hits[0].path, "Seeds.md"), "max_hits=1 keeps the best (title match): %s", n ? hits[0].path : "-");
    n = jidx_search(root, "a", hits, 5, 80, &st);
    CHECK(n == 0, "one-letter queries match nothing");
    n = jidx_search("/no/such/dir", "x y", hits, 5, 80, &st);
    CHECK(n == 0 || n == -1, "missing root");
    CHECK(jidx_search("/no/such/dir", "tomatoes", hits, 5, 80, &st) == -1, "missing root → -1");

    char terms[JIDX_MAX_TERMS][JIDX_TERM_MAX + 1];
    int nt = jidx_terms("Hello, WORLD! #Tag/sub hello x  verylongtermverylongtermverylongtermzz", terms, JIDX_MAX_TERMS);
    CHECK(nt == 4 && !strcmp(terms[0], "hello") && !strcmp(terms[1], "world") && !strcmp(terms[2], "#tag/sub") && strlen(terms[3]) == JIDX_TERM_MAX, "terms: %d %s %s %s", nt, terms[0], terms[1], nt > 2 ? terms[2] : "");

    char path[512];
    snprintf(path, sizeof path, "%s/Projects/Garden plan.md", root);
    scan_ctx c = {0};
    CHECK(jidx_scan(path, 31, on_scan, &c) == 0, "scan ok");
    CHECK(strstr(c.log, "16:tags|[garden, spring]|0@2;") && strstr(c.log, "16:status|active|0@3;"), "frontmatter: %s", c.log);
    CHECK(strstr(c.log, "1:Garden plan||1@5;") && strstr(c.log, "1:Watering||2@7;"), "headings: %s", c.log);
    CHECK(strstr(c.log, "8:buy seeds||0@9;") && strstr(c.log, "8:dig beds||1@10;"), "tasks: %s", c.log);
    CHECK(strstr(c.log, "2:Seeds|seed list|0@11;") && strstr(c.log, "2:Other.md|notes|0@11;"), "links: %s", c.log);
    CHECK(strstr(c.log, "4:garden||0@8;"), "tags: %s", c.log);

    snprintf(path, sizeof path, "%s/Seeds.md", root);
    memset(&c, 0, sizeof c);
    jidx_scan(path, JIDX_TAG | JIDX_LINK, on_scan, &c);
    CHECK(!strstr(c.log, "notatag") && !strstr(c.log, "NotALink") && !strstr(c.log, "nope") && !strstr(c.log, "code"), "fenced and inline code are skipped: %s", c.log);
    CHECK(strstr(c.log, "4:tag-one||") && strstr(c.log, "4:nested/tag||"), "tags with - and /: %s", c.log);
    CHECK(jidx_scan("/no/such.md", 31, on_scan, &c) == -1, "missing file → -1");

    const char *md = "---\ntitle: x\n---\n\n\n# Title\n\nSome **bold** and `code` with [[Target|alias]], [[Plain#Head]], [link](http://x.y) and ![pic](a.png).\n> quoted <b>html</b>\n\n\n\n```\n# not a heading\n```\n   - item\n";
    char out[512];
    size_t ol = jidx_strip(md, strlen(md), out, sizeof out);
    out[ol] = 0;
    const char *want = "title: x\n\nTitle\n\nSome bold and code with alias, Plain, link and pic.\nquoted html\n\n# not a heading\n   - item";
    CHECK(strcmp(out, want) == 0, "strip:\n---got---\n%s\n---want---\n%s", out, want);
    char inplace[512];
    strcpy(inplace, md);
    ol = jidx_strip(inplace, strlen(inplace), inplace, sizeof inplace);
    inplace[ol] = 0;
    CHECK(strcmp(inplace, want) == 0, "strip in place gives the same result");
    char tiny[8];
    ol = jidx_strip(md, strlen(md), tiny, sizeof tiny);
    CHECK(ol <= sizeof tiny, "strip respects cap (%zu)", ol);

    char cmd[600];
    snprintf(cmd, sizeof cmd, "rm -rf '%s'", root);
    if (system(cmd)) {}
    printf("%d passed, %d failed\n", passes, fails);
    return fails ? 1 : 0;
}
