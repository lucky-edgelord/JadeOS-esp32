# Build for Espressif's QEMU: emulated Ethernet instead of Wi-Fi, no USB, Python REPL on UART1.
list(APPEND SDKCONFIG_DEFAULTS ${MICROPY_BOARD_DIR}/sdkconfig.qemu)
list(APPEND MICROPY_DEF_BOARD
    JADE_QEMU=1
    MICROPY_HW_BOARD_NAME="JadeOS ESP32-S3 (QEMU)"
)
set(MICROPY_PY_TINYUSB OFF)
