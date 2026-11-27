#!/bin/sh
# Runs the launcher tests under the Q2's own busybox ash (qemu-user), whose
# `wait` and trap behaviour the launcher relies on.
#   q2/ssh/test-launcher-busybox.sh STOCK_ROOTFS_DIR [QEMU]
set -eu
root=$(cd "$1" && pwd)
qemu=${2:-$(dirname "$0")/../../build/qemu/qemu-mipsel-static}
export Q2_TEST_SHELL="$qemu -cpu P5600 -L $root $root/bin/busybox sh"
exec python3 -m unittest "$(dirname "$0")/test_launcher.py"
