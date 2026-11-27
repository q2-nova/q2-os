#!/bin/sh
# Build the RTL8733BS Wi-Fi module against a configured Q2 kernel build.
# usage: kernel/rtl8733bs/build.sh [MAKE_TARGETS...]     default: modules
#   env DSRC   prepared driver tree (default build/rtl8733bs/src)
#   env KOUT   kernel build directory from kernel/build.sh (default build/linux/out)
# The kernel source referenced by KOUT must also lie under build/.
set -eu
cd "$(dirname "$0")/../.."
dsrc=${DSRC:-build/rtl8733bs/src}
kout=${KOUT:-build/linux/out}
[ $# -gt 0 ] || set -- modules
case "$dsrc:$kout" in build/*:build/*) ;; *) echo 'DSRC/KOUT must be under build/' >&2; exit 1 ;; esac
[ -f "$kout/.config" ] || { echo "Missing configured kernel: $kout" >&2; exit 1; }
[ -f "$kout/Module.symvers" ] || { echo "Build the kernel's modules first: $kout" >&2; exit 1; }
grep -q '^CONFIG_CFG80211=[ym]' "$kout/.config" || { echo 'Kernel lacks CONFIG_CFG80211' >&2; exit 1; }
image=$(podman image inspect --format '{{.Id}}' q2-qemu-build)
podman run --rm --network none --security-opt label=disable -v "$PWD:/r" -w "/r/$dsrc" \
    -e KOUT="$kout" "$image" sh -ec '
  make -j"$(nproc)" ARCH=mips CROSS_COMPILE=mipsel-linux-gnu- KSRC="/r/$KOUT" "$@"
' sh "$@"
