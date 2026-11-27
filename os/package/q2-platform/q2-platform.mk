Q2_PLATFORM_VERSION = 1
Q2_PLATFORM_INSTALL_STAGING = YES
Q2_PLATFORM_SITE = $(BR2_EXTERNAL_Q2_PATH)/platform
Q2_PLATFORM_SITE_METHOD = local
Q2_PLATFORM_LICENSE = Proprietary

define Q2_PLATFORM_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-platform \
		-Wall -Wextra -Werror -fPIC -shared -o $(@D)/libq2platform.so $(@D)/display.c $(@D)/input.c $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-platform \
		-Wall -Wextra -Werror -o $(@D)/q2-usb-status $(@D)/usb-status.c -L$(@D) -lq2platform $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -ffile-prefix-map=$(@D)=/q2-platform \
		-Wall -Wextra -Werror -o $(@D)/q2-usb-auto-events $(@D)/usb-auto-events.c $(TARGET_LDFLAGS)
endef

define Q2_PLATFORM_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/libq2platform.so $(TARGET_DIR)/usr/lib/libq2platform.so
	$(INSTALL) -D -m 0755 $(@D)/q2-usb-status $(TARGET_DIR)/usr/sbin/q2-usb-status
	$(INSTALL) -D -m 0755 $(@D)/usb-status-control $(TARGET_DIR)/usr/sbin/q2-usb-status-control
	$(INSTALL) -D -m 0755 $(@D)/usb-auto $(TARGET_DIR)/usr/sbin/q2-usb-auto
	$(INSTALL) -D -m 0755 $(@D)/q2-usb-auto-events $(TARGET_DIR)/usr/sbin/q2-usb-auto-events
endef

define Q2_PLATFORM_INSTALL_STAGING_CMDS
	$(INSTALL) -D -m 0755 $(@D)/libq2platform.so $(STAGING_DIR)/usr/lib/libq2platform.so
	$(INSTALL) -D -m 0644 $(@D)/input.h $(STAGING_DIR)/usr/include/q2-input.h
endef

$(eval $(generic-package))
