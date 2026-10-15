# Make build (unix port tests): make -C ports/unix USER_C_MODULES=<repo>/firmware/modules
JADECORE_DIR := $(USERMOD_DIR)
SRC_USERMOD_C += $(JADECORE_DIR)/jadecore.c $(JADECORE_DIR)/jade_vfs.c
SRC_USERMOD_LIB_C += $(JADECORE_DIR)/jade_idx.c $(JADECORE_DIR)/jade_bridge.c
CFLAGS_USERMOD += -I$(JADECORE_DIR)
LDFLAGS_USERMOD += -lpthread
