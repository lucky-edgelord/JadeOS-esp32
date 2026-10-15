// Shell → Python bridge. See jade_bridge.h.
#include "jade_bridge.h"

#include <stdlib.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
typedef SemaphoreHandle_t jb_lock_t;
typedef SemaphoreHandle_t jb_signal_t;
static jb_lock_t lock_new(void) { return xSemaphoreCreateMutex(); }
static void lock_take(jb_lock_t l) { xSemaphoreTake(l, portMAX_DELAY); }
static void lock_give(jb_lock_t l) { xSemaphoreGive(l); }
static void signal_new(jb_signal_t *s) { *s = xSemaphoreCreateBinary(); }
static void signal_reset(jb_signal_t *s) { xSemaphoreTake(*s, 0); }
static void signal_give(jb_signal_t *s) { xSemaphoreGive(*s); }
// called with the lock held; releases it while waiting. Returns 1 if signalled.
static int signal_wait(jb_signal_t *s, jb_lock_t l, unsigned ms) {
    lock_give(l);
    int ok = xSemaphoreTake(*s, pdMS_TO_TICKS(ms)) == pdTRUE;
    lock_take(l);
    return ok;
}
#else
#include <errno.h>
#include <pthread.h>
#include <time.h>
typedef pthread_mutex_t *jb_lock_t;
typedef struct { pthread_cond_t cond; int fired; } jb_signal_t;
static pthread_mutex_t g_mutex = PTHREAD_MUTEX_INITIALIZER;
static jb_lock_t lock_new(void) { return &g_mutex; }
static void lock_take(jb_lock_t l) { pthread_mutex_lock(l); }
static void lock_give(jb_lock_t l) { pthread_mutex_unlock(l); }
static void signal_new(jb_signal_t *s) { pthread_cond_init(&s->cond, NULL); s->fired = 0; }
static void signal_reset(jb_signal_t *s) { s->fired = 0; }
static void signal_give(jb_signal_t *s) { s->fired = 1; pthread_cond_signal(&s->cond); }
static int signal_wait(jb_signal_t *s, jb_lock_t l, unsigned ms) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_sec += ms / 1000;
    ts.tv_nsec += (long)(ms % 1000) * 1000000L;
    if (ts.tv_nsec >= 1000000000L) {
        ts.tv_sec++;
        ts.tv_nsec -= 1000000000L;
    }
    while (!s->fired) {
        if (pthread_cond_timedwait(&s->cond, l, &ts) == ETIMEDOUT) {
            break;
        }
    }
    return s->fired;
}
#endif

enum { FREE = 0, PENDING, TAKEN, DONE, ABANDONED };

typedef struct {
    int state;
    int id;
    unsigned seq;                 // order of arrival
    char req[JBRIDGE_REQ_MAX];
    char *reply;
    int status;
    jb_signal_t done;
} slot_t;

static slot_t g_slots[JBRIDGE_SLOTS];
static jb_lock_t g_lock;
static int g_next_id = 1;
static unsigned g_seq = 0;
static int g_ready = 0;

void jbridge_init(void) {
    if (g_ready) {
        return;
    }
    g_lock = lock_new();
    for (int i = 0; i < JBRIDGE_SLOTS; i++) {
        memset(&g_slots[i], 0, sizeof(g_slots[i]));
        signal_new(&g_slots[i].done);
    }
    g_ready = 1;
}

int jbridge_call(const char *request, unsigned timeout_ms, char **reply) {
    jbridge_init();
    *reply = NULL;
    lock_take(g_lock);
    slot_t *s = NULL;
    for (int i = 0; i < JBRIDGE_SLOTS; i++) {
        if (g_slots[i].state == FREE) {
            s = &g_slots[i];
            break;
        }
    }
    for (int i = 0; !s && i < JBRIDGE_SLOTS; i++) {
        if (g_slots[i].state == ABANDONED) {   // its late reply (if any) will no longer match the id
            s = &g_slots[i];
        }
    }
    if (!s) {
        lock_give(g_lock);
        return -1;
    }
    s->state = PENDING;
    s->id = g_next_id++;
    if (g_next_id > 0x3fffffff) {
        g_next_id = 1;
    }
    s->seq = g_seq++;
    strncpy(s->req, request, sizeof(s->req) - 1);
    s->req[sizeof(s->req) - 1] = '\0';
    s->reply = NULL;
    s->status = 0;
    signal_reset(&s->done);
    int got = signal_wait(&s->done, g_lock, timeout_ms);
    int ret;
    if (got && s->state == DONE) {
        *reply = s->reply;
        ret = s->reply ? s->status : -3;
        s->reply = NULL;
        s->state = FREE;
    } else if (s->state == PENDING) {   // nobody took it: just withdraw it
        s->state = FREE;
        ret = -2;
    } else {                            // Python is working on it; drop the answer when it comes
        s->state = ABANDONED;
        ret = -2;
    }
    lock_give(g_lock);
    return ret;
}

int jbridge_take(char *buf, size_t cap) {
    jbridge_init();
    lock_take(g_lock);
    slot_t *best = NULL;
    for (int i = 0; i < JBRIDGE_SLOTS; i++) {
        slot_t *s = &g_slots[i];
        if (s->state == PENDING && (!best || (int)(s->seq - best->seq) < 0)) {
            best = s;
        }
    }
    int id = 0;
    if (best) {
        best->state = TAKEN;
        id = best->id;
        if (cap) {
            strncpy(buf, best->req, cap - 1);
            buf[cap - 1] = '\0';
        }
    }
    lock_give(g_lock);
    return id;
}

int jbridge_reply(int id, int status, const char *text, size_t len) {
    jbridge_init();
    if (len > JBRIDGE_REPLY_MAX) {
        len = JBRIDGE_REPLY_MAX;
    }
    lock_take(g_lock);
    int ret = -1;
    for (int i = 0; i < JBRIDGE_SLOTS; i++) {
        slot_t *s = &g_slots[i];
        if (s->id != id || (s->state != TAKEN && s->state != ABANDONED)) {
            continue;
        }
        if (s->state == ABANDONED) {
            s->state = FREE;
            break;
        }
        s->reply = malloc(len + 1);
        if (s->reply) {
            memcpy(s->reply, text, len);
            s->reply[len] = '\0';
        }
        s->status = status;
        s->state = DONE;
        signal_give(&s->done);
        ret = 0;
        break;
    }
    lock_give(g_lock);
    return ret;
}

int jbridge_pending(void) {
    jbridge_init();
    lock_take(g_lock);
    int n = 0;
    for (int i = 0; i < JBRIDGE_SLOTS; i++) {
        n += g_slots[i].state == PENDING;
    }
    lock_give(g_lock);
    return n;
}
