// JadeOS board glue: starts the BreezyBox shell on UART0 before MicroPython, and adds Jade's shell
// commands, which talk to Python through the bridge (jade_bridge.c).
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_console.h"
#include "esp_log.h"
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "esp_littlefs.h"

#include "breezybox.h"
#include "jade_bridge.h"

void boardctrl_startup(void);   // MicroPython's default start-up (NVS, flash size)

// ---------------------------------------------------------------- hooks used by jadecore
void jidx_yield_hook(void) {
    vTaskDelay(1);   // a long vault search lets other tasks (Wi-Fi, the shell) run
}

int jade_platform_fs_usage(const char *path, size_t *total, size_t *used) {
    (void)path;
    return esp_littlefs_info("storage", total, used) == ESP_OK ? 0 : -1;
}

static SemaphoreHandle_t s_exec_lock;

// Run one BreezyBox command for Python (jadecore.shell), capturing what it prints.
int jade_platform_shell(const char *line, char *out, size_t cap, size_t *len) {
    *len = 0;
    while (*line == ' ') {
        line++;
    }
    if (strncmp(line, "jade", 4) == 0) {
        // Python is waiting for this call, so it couldn't answer a `jade` command: refuse
        const char *msg = "jade commands can't be run from Python\n";
        *len = strlen(msg) < cap ? strlen(msg) : cap;
        memcpy(out, msg, *len);
        return 126;
    }
    if (cap < 2) {
        return -1;
    }
    xSemaphoreTake(s_exec_lock, portMAX_DELAY);
    FILE *mem = fmemopen(out, cap, "w");
    if (!mem) {
        xSemaphoreGive(s_exec_lock);
        return -1;
    }
    setvbuf(mem, NULL, _IONBF, 0);
    // stdout/stderr are per-task in ESP-IDF's newlib: only this task's output is captured
    FILE *old_out = stdout, *old_err = stderr;
    stdout = mem;
    stderr = mem;
    int st = breezybox_exec_line(line);
    fflush(mem);
    long n = ftell(mem);
    stdout = old_out;
    stderr = old_err;
    fclose(mem);
    xSemaphoreGive(s_exec_lock);
    *len = n < 0 ? 0 : (size_t)n >= cap ? cap - 1 : (size_t)n;
    return st;
}

// ---------------------------------------------------------------- shell commands
// jade <cmd> [args...]   and the shortcuts jade-ask, jade-search, jade-tool, jade-py
static int cmd_jade(int argc, char **argv) {
    char req[JBRIDGE_REQ_MAX];
    size_t n = 0;
    const char *sub = NULL;
    if (strcmp(argv[0], "jade") != 0) {
        sub = argv[0] + 5;   // "jade-ask" → "ask"
        n = (size_t)snprintf(req, sizeof(req), "%s", sub);
    }
    for (int i = 1; i < argc && n < sizeof(req) - 1; i++) {
        n += (size_t)snprintf(req + n, sizeof(req) - n, "%s%s", n ? " " : "", argv[i]);
    }
    if (n >= sizeof(req)) {
        n = sizeof(req) - 1;
    }
    req[n] = '\0';
    if (!n) {
        strcpy(req, "help");
    }
    unsigned timeout = strncmp(req, "ask", 3) == 0 ? 180000 : 30000;
    char *reply = NULL;
    int st = jbridge_call(req, timeout, &reply);
    if (st == -1) {
        printf("jade: busy, try again\n");
        return 1;
    }
    if (st == -2) {
        printf("jade: no answer from Python (is Jade running? see /root/main.py and /root/jade/SAFE)\n");
        return 1;
    }
    if (st == -3 || !reply) {
        printf("jade: out of memory\n");
        return 1;
    }
    fputs(reply, stdout);
    if (reply[0] && reply[strlen(reply) - 1] != '\n') {
        fputc('\n', stdout);
    }
    free(reply);
    return st;
}

static int cmd_managed(int argc, char **argv) {
    (void)argc;
    if (strcmp(argv[0], "wifi") == 0) {
        printf("Wi-Fi is managed by Jade: set it in the dashboard or /root/jade/config.json, then reboot.\n"
               "Current network: ");
        char *argv2[] = {"jade", "py", "__import__('jade.sysinfo').sysinfo.net_info"};
        return cmd_jade(3, argv2);
    }
    printf("%s is turned off in JadeOS: Jade's API already serves the device (see the dashboard).\n", argv[0]);
    return 1;
}

static const esp_console_cmd_t s_cmds[] = {
    { .command = "jade", .help = "Talk to Jade: jade ask|search|tool|tools|py|status|history|clear|help", .hint = "<cmd> [args]", .func = &cmd_jade },
    { .command = "jade-ask", .help = "Ask Jade (searches the vault, may use tools)", .hint = "<question>", .func = &cmd_jade },
    { .command = "jade-search", .help = "Search the vault", .hint = "<words>", .func = &cmd_jade },
    { .command = "jade-tool", .help = "Run one of Jade's tools", .hint = "<name> [json]", .func = &cmd_jade },
    { .command = "jade-py", .help = "Run Python on the device", .hint = "<code>", .func = &cmd_jade },
    // registered after BreezyBox's own, so these replace its wifi and httpd
    { .command = "wifi", .help = "Wi-Fi (managed by Jade)", .hint = NULL, .func = &cmd_managed },
    { .command = "httpd", .help = "File server (off: Jade's API serves the device)", .hint = NULL, .func = &cmd_managed },
};

void jade_board_startup(void) {
    boardctrl_startup();
    jbridge_init();
    s_exec_lock = xSemaphoreCreateMutex();

    // a real UART driver behind stdin/stdout, so the shell's reads block instead of spinning
    const int uart = CONFIG_ESP_CONSOLE_UART_NUM;
    fflush(stdout);
    fsync(fileno(stdout));
    if (uart_driver_install(uart, 1024, 0, 0, NULL, 0) == ESP_OK) {
        uart_vfs_dev_use_driver(uart);
    }
    uart_vfs_dev_port_set_rx_line_endings(uart, ESP_LINE_ENDINGS_CR);
    uart_vfs_dev_port_set_tx_line_endings(uart, ESP_LINE_ENDINGS_CRLF);
    setvbuf(stdin, NULL, _IONBF, 0);

    printf("\nJadeOS: starting the BreezyBox shell (type 'jade help')\n");
    if (breezybox_start_stdio_ex(16384, 2, s_cmds, sizeof(s_cmds) / sizeof(s_cmds[0])) != ESP_OK) {
        printf("JadeOS: BreezyBox failed to start; Python will still run\n");
    }
}
