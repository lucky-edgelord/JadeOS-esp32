// Shell → Python bridge.
//
// The BreezyBox shell runs in its own FreeRTOS task, but Python objects may only be touched by the
// MicroPython task. So shell commands like `jade ask ...` don't call into the VM: they put a request in
// a small queue and wait. Jade's asyncio loop polls the queue (jadecore.bridge_take), does the work
// and answers (jadecore.bridge_reply), which wakes the waiting shell command.
//
// The same code runs on Linux with pthreads, so the unix port can test it.
#pragma once

#include <stddef.h>

#define JBRIDGE_SLOTS     4     // requests in flight
#define JBRIDGE_REQ_MAX   512   // request text
#define JBRIDGE_REPLY_MAX 8192  // longest reply kept (more is cut)

void jbridge_init(void);

// Shell side. Sends `request` and blocks up to timeout_ms for the reply. On success returns the
// reply's status (>= 0) and *reply points to a malloc'd, NUL-terminated string the caller frees.
// Returns -1 if all slots are busy, -2 on timeout (the late reply is then dropped), -3 out of memory.
int jbridge_call(const char *request, unsigned timeout_ms, char **reply);

// Python side.
// The oldest waiting request: copies its text into buf and returns its id (> 0), or 0 if none.
int jbridge_take(char *buf, size_t cap);
// Answer request `id`. Returns 0, or -1 if that request is unknown or already gave up.
int jbridge_reply(int id, int status, const char *text, size_t len);
// Requests waiting to be taken.
int jbridge_pending(void);
