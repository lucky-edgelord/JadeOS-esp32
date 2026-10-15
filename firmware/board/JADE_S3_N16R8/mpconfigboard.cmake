# JadeOS on an ESP32-S3 N16R8 (16 MB flash, 8 MB octal PSRAM), e.g. ESP32-S3-DevKitC-1-N16R8.
set(IDF_TARGET esp32s3)

# the partition table lives next to this file; ESP-IDF wants its path in the sdkconfig
file(WRITE ${CMAKE_BINARY_DIR}/sdkconfig.jade_partitions
    "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=\"${MICROPY_BOARD_DIR}/partitions-jade.csv\"\n")

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.spiram_quad
    boards/sdkconfig.spiram_oct
    boards/sdkconfig.240mhz
    boards/sdkconfig.flash_qio_80m
    ${MICROPY_BOARD_DIR}/sdkconfig.board
    ${CMAKE_BINARY_DIR}/sdkconfig.jade_partitions
)

set(MICROPY_SOURCE_BOARD
    ${MICROPY_BOARD_DIR}/jade_board.c
)

set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/manifest.py)
