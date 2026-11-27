#!/bin/sh
# Build only the recovery trial helper; does not stage or deploy the player.
set -eu
cd "$(dirname "$0")/../.."
root=$PWD
export Q2_SYSROOT=${Q2_SYSROOT:-$root/build/sysroot}
nightly=${Q2_NIGHTLY:-nightly-2026-08-21}
llvm=$(rustc +"$nightly" --print sysroot)/lib/rustlib/x86_64-unknown-linux-gnu/bin
export Q2_LLD_DIR="$llvm/gcc-ld"
mkdir -p build/recovery
export Q2_START="$root/build/recovery/start.o"
q2/toolchain/q2-cc -O2 -fno-pic -c q2/toolchain/start.c -o "$Q2_START"
q2/toolchain/q2-cc -O2 -Wall -Wextra -Werror -fno-pic -c q2/recovery/trial.c -o build/recovery/trial.o
q2/toolchain/q2-link build/recovery/trial.o -lc -o build/recovery/trial

q2/toolchain/q2-cc -O2 -Wall -Wextra -Werror -fno-pic -c q2/recovery/watchdog-probe.c -o build/recovery/watchdog-probe.o
q2/toolchain/q2-link build/recovery/watchdog-probe.o -lc -o build/recovery/watchdog-probe
