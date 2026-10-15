#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "JadeOS ESP32-S3 N16R8"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32-S3"

// Start BreezyBox and the shell bridge before MicroPython (see jade_board.c).
#define MICROPY_BOARD_STARTUP               jade_board_startup
void jade_board_startup(void);

#if JADE_QEMU
// The emulator has no USB: Python's REPL goes to UART1 (the second -serial), BreezyBox keeps UART0.
#define MICROPY_HW_ENABLE_USBDEV            (0)
#define MICROPY_HW_USB_CDC                  (0)
#define MICROPY_HW_ESP_USB_SERIAL_JTAG      (0)
#define MICROPY_HW_ENABLE_UART_REPL         (1)
#define MICROPY_HW_UART_REPL                (1)
#else
// UART0 belongs to the BreezyBox shell; Python's REPL is on the native USB port.
#define MICROPY_HW_ENABLE_UART_REPL         (0)
#endif

#define MICROPY_HW_I2C0_SCL                 (9)
#define MICROPY_HW_I2C0_SDA                 (8)
