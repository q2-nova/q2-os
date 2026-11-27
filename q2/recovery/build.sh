#!/bin/sh
# Build the recovery trial helper and the watchdog probe for the stock
# firmware's glibc; does not stage or deploy the player.
#   q2/recovery/build.sh Q2_TOOLCHAIN_CHECKOUT
# Uses q2/toolchain/{q2-cc,q2-link,start.c} of a q2-toolchain checkout.
# env Q2_SYSROOT  the stock firmware's glibc sysroot (default build/sysroot)
set -eu
[ $# -eq 1 ] || { echo 'usage: q2/recovery/build.sh Q2_TOOLCHAIN_CHECKOUT' >&2; exit 2; }
toolchain=$(cd "$1" && pwd)/q2/toolchain
[ -x "$toolchain/q2-cc" ] && [ -x "$toolchain/q2-link" ] && [ -f "$toolchain/start.c" ] ||
    { echo "$1: not a q2-toolchain checkout" >&2; exit 2; }
cd "$(dirname "$0")/../.."
root=$PWD
export Q2_SYSROOT=${Q2_SYSROOT:-$root/build/sysroot}
nightly=${Q2_NIGHTLY:-nightly-2026-08-21}
llvm=$(rustc +"$nightly" --print sysroot)/lib/rustlib/x86_64-unknown-linux-gnu/bin
export Q2_LLD_DIR="$llvm/gcc-ld"
mkdir -p build/recovery
export Q2_START="$root/build/recovery/start.o"
"$toolchain/q2-cc" -O2 -fno-pic -c "$toolchain/start.c" -o "$Q2_START"
"$toolchain/q2-cc" -O2 -Wall -Wextra -Werror -fno-pic -c q2/recovery/trial.c -o build/recovery/trial.o
"$toolchain/q2-link" build/recovery/trial.o -lc -o build/recovery/trial

"$toolchain/q2-cc" -O2 -Wall -Wextra -Werror -fno-pic -c q2/recovery/watchdog-probe.c -o build/recovery/watchdog-probe.o
"$toolchain/q2-link" build/recovery/watchdog-probe.o -lc -o build/recovery/watchdog-probe
