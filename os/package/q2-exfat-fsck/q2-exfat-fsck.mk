################################################################################
#
# q2-exfat-fsck: fsck.exfat from exfatprogs, without the other exFAT tools
#
################################################################################

Q2_EXFAT_FSCK_VERSION = 1.4.3
Q2_EXFAT_FSCK_SOURCE = exfatprogs-$(Q2_EXFAT_FSCK_VERSION).tar.xz
Q2_EXFAT_FSCK_SITE = https://github.com/exfatprogs/exfatprogs/releases/download/$(Q2_EXFAT_FSCK_VERSION)
Q2_EXFAT_FSCK_LICENSE = GPL-2.0+
Q2_EXFAT_FSCK_LICENSE_FILES = COPYING
Q2_EXFAT_FSCK_CPE_ID_VENDOR = namjaejeon
Q2_EXFAT_FSCK_CPE_ID_PRODUCT = exfatprogs

# The image formats nothing: build the library and the checker only.
# configure requires libblkid, which only mkfs.exfat uses; these non-empty
# values satisfy its check without a libblkid package.
Q2_EXFAT_FSCK_CONF_ENV = BLKID_CFLAGS=" " BLKID_LIBS=" "

define Q2_EXFAT_FSCK_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/lib
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/fsck
endef

define Q2_EXFAT_FSCK_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/fsck/fsck.exfat $(TARGET_DIR)/usr/sbin/fsck.exfat
endef

$(eval $(autotools-package))
