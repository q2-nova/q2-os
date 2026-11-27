#!/bin/sh
# Runs the launcher tests under the Q2's own busybox ash (qemu-user), whose
# `wait` and trap behaviour the launcher relies on.
#   os/test-launcher-busybox.sh STOCK_ROOTFS_DIR [QEMU]
# STOCK_ROOTFS_DIR: an extracted stock firmware root filesystem (its
# bin/busybox and loader). QEMU defaults to build/qemu/qemu-mipsel-static.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$1" && pwd)
qemu=${2:-$here/../build/qemu/qemu-mipsel-static}
export Q2_TEST_SHELL="$qemu -cpu P5600 -L $root $root/bin/busybox sh"
exec python3 -m unittest "$here/test_launcher.py"
