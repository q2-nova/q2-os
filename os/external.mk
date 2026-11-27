include $(sort $(wildcard $(BR2_EXTERNAL_Q2_PATH)/package/*/*.mk))

# The bootstrap compiler does not emit GNU-stack notes by default on MIPS.
# glibc also has separate DSO/loader link flags; configure LDFLAGS alone does
# not reach those links. Keep CRT, nonshared code, DSOs and loader consistent.
GLIBC_EXTRA_CFLAGS += -Wa,--noexecstack
GLIBC_CONF_ENV += libc_cv_as_noexecstack=yes
GLIBC_MAKE_OPTS += LDFLAGS.so=-Wl,-z,relro,-z,noexecstack
GLIBC_MAKE_OPTS += LDFLAGS-rtld=-Wl,-z,relro,-z,noexecstack

# ALSA installs in the loader's standard /usr/lib search path. Libtool's
# temporary build-directory RPATH otherwise leaves path-length-dependent
# padding after Buildroot sanitizes it (aserver and libatopology).
define Q2_ALSA_NO_BUILD_RPATH
	$(SED) 's/^hardcode_libdir_flag_spec=.*/hardcode_libdir_flag_spec=""/' $(@D)/libtool
	$(SED) 's/^runpath_var=.*/runpath_var=""/' $(@D)/libtool
endef
ALSA_LIB_POST_CONFIGURE_HOOKS += Q2_ALSA_NO_BUILD_RPATH
