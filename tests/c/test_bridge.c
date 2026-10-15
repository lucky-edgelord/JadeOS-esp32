// Host test for jade_bridge.c: shell threads call, a "Python" thread answers. Run under TSan and ASan.
#include "../../firmware/modules/jadecore/jade_bridge.h"

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static _Atomic int fails = 0, passes = 0;
#define CHECK(c, ...) do { if (c) { passes++; } else { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static _Atomic int stop_server = 0;
static _Atomic int served = 0;

// answers "echo X" with "X", "slow" after 300 ms, "big" with 20 KB, ignores "ignore"
static void *server(void *arg) {
    (void)arg;
    char buf[JBRIDGE_REQ_MAX];
    while (!stop_server) {
        int id = jbridge_take(buf, sizeof buf);
        if (!id) {
            usleep(1000);
            continue;
        }
        served++;
        if (strncmp(buf, "echo ", 5) == 0) {
            jbridge_reply(id, 0, buf + 5, strlen(buf + 5));
        } else if (strcmp(buf, "slow") == 0) {
            usleep(300000);
            CHECK(jbridge_reply(id, 0, "late", 4) == -1, "a reply after the caller gave up is refused");
        } else if (strcmp(buf, "big") == 0) {
            static char big[20000];
            memset(big, 'z', sizeof big);
            jbridge_reply(id, 3, big, sizeof big);
        } else if (strcmp(buf, "ignore") == 0) {
            // never answered
        } else {
            jbridge_reply(id, 1, "unknown", 7);
        }
    }
    return NULL;
}

static void *client(void *arg) {
    int n = (int)(long)arg;
    for (int i = 0; i < 50; i++) {
        char req[64], want[64];
        snprintf(req, sizeof req, "echo c%d-%d", n, i);
        snprintf(want, sizeof want, "c%d-%d", n, i);
        char *reply = NULL;
        int st = jbridge_call(req, 5000, &reply);
        if (st == -1) {      // all slots busy: retry
            i--;
            usleep(500);
            continue;
        }
        CHECK(st == 0 && reply && strcmp(reply, want) == 0, "client %d got %s for %s (st %d)", n, reply ? reply : "(null)", req, st);
        free(reply);
    }
    return NULL;
}

static void *ignored_call(void *arg) {
    (void)arg;
    char *reply = NULL;
    int st = jbridge_call("ignore", 400, &reply);
    CHECK(st == -2, "ignored request times out (%d)", st);
    return NULL;
}

int main(void) {
    jbridge_init();
    char *reply = NULL;
    CHECK(jbridge_call("echo nobody", 50, &reply) == -2 && !reply, "timeout when nobody answers");
    CHECK(jbridge_pending() == 0, "a timed-out request is withdrawn");

    pthread_t srv;
    pthread_create(&srv, NULL, server, NULL);

    CHECK(jbridge_call("echo hello", 2000, &reply) == 0 && strcmp(reply, "hello") == 0, "echo");
    free(reply);
    CHECK(jbridge_call("what", 2000, &reply) == 1 && strcmp(reply, "unknown") == 0, "status passes through");
    free(reply);
    CHECK(jbridge_call("big", 2000, &reply) == 3 && strlen(reply) == JBRIDGE_REPLY_MAX, "reply cut to JBRIDGE_REPLY_MAX");
    free(reply);
    CHECK(jbridge_call("slow", 100, &reply) == -2 && !reply, "caller times out while Python works");
    usleep(400000);   // let the late reply arrive and free the slot

    char longreq[2000];
    memset(longreq, 'q', sizeof longreq);
    memcpy(longreq, "echo ", 5);
    longreq[sizeof longreq - 1] = 0;
    CHECK(jbridge_call(longreq, 2000, &reply) == 0 && strlen(reply) == JBRIDGE_REQ_MAX - 1 - 5, "long requests are cut, %zu", reply ? strlen(reply) : 0);
    free(reply);

    pthread_t cl[6];
    for (long i = 0; i < 6; i++) {
        pthread_create(&cl[i], NULL, client, (void *)i);
    }
    for (int i = 0; i < 6; i++) {
        pthread_join(cl[i], NULL);
    }

    // fill every slot with requests that are never answered: the next caller is told the bridge is busy
    pthread_t ign[JBRIDGE_SLOTS];
    for (int i = 0; i < JBRIDGE_SLOTS; i++) {
        pthread_create(&ign[i], NULL, ignored_call, NULL);
    }
    usleep(100000);
    CHECK(jbridge_call("echo x", 100, &reply) == -1, "busy when every slot is in use");
    for (int i = 0; i < JBRIDGE_SLOTS; i++) {
        pthread_join(ign[i], NULL);
    }
    CHECK(jbridge_call("echo again", 2000, &reply) == 0 && strcmp(reply, "again") == 0, "slots are reusable after timeouts");
    free(reply);
    stop_server = 1;
    pthread_join(srv, NULL);
    printf("%d passed, %d failed (served %d)\n", (int)passes, (int)fails, (int)served);
    return fails ? 1 : 0;
}
