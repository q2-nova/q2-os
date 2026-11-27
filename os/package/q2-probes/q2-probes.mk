Q2_PROBES_VERSION = 1
Q2_PROBES_DEPENDENCIES = alsa-lib q2-platform
Q2_PROBES_INSTALL_STAGING = YES
Q2_PROBES_SITE = $(BR2_EXTERNAL_Q2_PATH)/probes
Q2_PROBES_SITE_METHOD = local
Q2_PROBES_LICENSE = GPL-3.0+
Q2_PROBES_LICENSE_FILES = LICENSE

define Q2_PROBES_COPY_LICENSE
	$(INSTALL) -D -m 0644 $(BR2_EXTERNAL_Q2_PATH)/../LICENSE $(Q2_PROBES_DIR)/LICENSE
endef
Q2_PROBES_POST_RSYNC_HOOKS += Q2_PROBES_COPY_LICENSE
Q2_PROBES_PRE_LEGAL_INFO_HOOKS += Q2_PROBES_COPY_LICENSE

define Q2_PROBES_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -Wall -Wextra -Werror -o $(@D)/q2-player-supervisor $(@D)/player-supervisor.c $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -Wall -Wextra -Werror \
		-o $(@D)/q2-memory-guard $(@D)/memory-guard.c $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -Wall -Wextra -Werror \
		-o $(@D)/q2-rtc-probe $(@D)/rtc.c $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -Wall -Wextra -Werror \
		-o $(@D)/q2-watchdog-guard $(@D)/watchdog-guard.c $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -Wall -Wextra -Werror \
		-o $(@D)/q2-input-probe $(@D)/input.c $(TARGET_LDFLAGS) -lq2platform
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -Wall -Wextra -Werror \
		-o $(@D)/q2-alsa-probe $(@D)/alsa.c $(TARGET_LDFLAGS) -lasound
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -std=gnu11 -Wall -Wextra -Werror \
		-o $(@D)/q2-abi-probe $(@D)/abi.c $(TARGET_LDFLAGS) -pthread -lm -ldl
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -Wall -Wextra -Werror -fPIC -shared \
		-o $(@D)/libq2probe.so $(@D)/ffi.c $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-probes -Wall -Wextra -Werror \
		-o $(@D)/q2-seed-entropy $(@D)/entropy.c $(TARGET_LDFLAGS)
endef

define Q2_PROBES_INSTALL_STAGING_CMDS
	$(INSTALL) -D -m 0755 $(@D)/libq2probe.so $(STAGING_DIR)/usr/lib/libq2probe.so
endef

define Q2_PROBES_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/q2-player-supervisor $(TARGET_DIR)/usr/sbin/q2-player-supervisor
	$(INSTALL) -D -m 0755 $(@D)/q2-memory-guard $(TARGET_DIR)/usr/sbin/q2-memory-guard
	$(INSTALL) -D -m 0755 $(@D)/q2-rtc-probe $(TARGET_DIR)/usr/bin/q2-rtc-probe
	$(INSTALL) -D -m 0755 $(@D)/q2-watchdog-guard $(TARGET_DIR)/usr/sbin/q2-watchdog-guard
	$(INSTALL) -D -m 0755 $(@D)/q2-input-probe $(TARGET_DIR)/usr/bin/q2-input-probe
	$(INSTALL) -D -m 0755 $(@D)/q2-alsa-probe $(TARGET_DIR)/usr/bin/q2-alsa-probe
	$(INSTALL) -D -m 0755 $(@D)/q2-abi-probe $(TARGET_DIR)/usr/bin/q2-abi-probe
	$(INSTALL) -D -m 0755 $(@D)/libq2probe.so $(TARGET_DIR)/usr/lib/libq2probe.so
	$(INSTALL) -D -m 0755 $(@D)/q2-seed-entropy $(TARGET_DIR)/usr/sbin/q2-seed-entropy
endef

$(eval $(generic-package))
