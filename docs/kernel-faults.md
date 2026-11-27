# Kernel fault suites

Three emulator suites check how the production kernel handles microSD errors,
SPI NAND errors and the watchdog. They run the production Linux revision on
the Q2 QEMU machine, inject faults through its `q2ctl` channel and check both
what the guest sees and what reaches the generated media. They are emulation
results: they cover the kernel's handling of the errors the emulator models,
not the behaviour of a physical card, flash chip or watchdog.

| Suite | Command | Time in CI |
| --- | --- | --- |
| microSD and NAND fault recovery | `python3 os/test_storage_faults.py [--suite sd\|nand\|all]` | 20 s |
| Watchdog UAPI | `python3 os/test_watchdog_uapi.py [--case uapi\|nowayout\|all]` | 70 s |

The [kernel fault workflow](../.github/workflows/kernel-faults.yml) builds the
fault kernel and runs both suites on pull requests that change their inputs,
on `main`, and on request; building the kernel takes about 5 minutes of
its 7. Host checks run `python3 os/test_kernel_faults.py`,
which tests the builder, the initramfs composition, the media layouts and the
harness's QEMU command lines without an emulator.

## The fault kernel

`os/kernel_faults.py build` compiles the `linux_production` commit and
`shanling_q2_production_defconfig` from [the source lock](../os/sources.json)
in the pinned kernel container, with one configuration change:
`MIPS_CMDLINE_DTB_EXTEND` replaces `MIPS_CMDLINE_FROM_DTB`, so the emulator's
`-append` extends the DT command line. The watchdog suite uses it to set
`ingenic_x2000_wdt.nowayout=1`. The embedded initramfs is the released rescue
archive (busybox and its libraries) with:

- `/init` from `os/kernel-faults/init`: mounts the kernel filesystems, prints
  `Q2_FAULT_SHELL_READY` on the console (UART3) and keeps a root shell on
  UART0;
- `/bin/q2-sdfault`, `/bin/q2-nandfault` and `/bin/q2-wdtcheck`, freestanding
  probes built from `os/kernel-faults/*.c` against the kernel's own UAPI
  headers, with no libc;
- a `/dev/console` node.

The build writes `build/os/kernel-faults/kernel/` with `xImage`,
`linux.config`, the initramfs, the probes and `artifact.json`, which records
the Linux revision, the container, the rescue archive and the digests of
every input in `os/kernel-faults/`. The suites refuse a kernel whose recorded
inputs differ from the checkout.

`os/kernel_faults.py fetch` downloads the inputs pinned in
`os/kernel-faults/inputs.json` and checks their digests: the Q2 QEMU release
from q2-toolchain, whose `qemu-artifact.json` must name the source lock's
`qemu_installed` commit, and the rescue archive of a q2-os release.

```sh
python3 os/kernel_faults.py fetch
python3 os/install/prepare.py --dest build/os/fault-kernel-src
python3 os/kernel_faults.py build --kernel-source build/os/fault-kernel-src
python3 os/test_storage_faults.py
python3 os/test_watchdog_uapi.py
```

The released QEMU is built on Ubuntu 24.04 and needs the runtime libraries
its `qemu-artifact.json` lists. On another host, pass `--container IMAGE` to
the suites to run QEMU inside an image that has them, such as the kernel
container `q2-qemu-build`; `build --container IMAGE` reuses an existing kernel
container instead of building the pinned recipe. Each run writes `console.log`,
`shell.log`, `qemu.log` and `result.json` to
`build/os/kernel-faults/runs/SUITE-ID/`. The watchdog suite holds the
harness's guest clock lock exclusively, so no other guest of this checkout
runs beside it.

## microSD

The card is a generated 64 MiB image: an exFAT volume over the first 32 MiB
(the production kernel has exFAT built in) and two raw 64 KiB areas filled
with `a5` at 48 MiB and 52 MiB. The probe reads and writes the raw areas with
`O_DIRECT`, so each call reaches the controller. The emulator applies faults
to read and write commands on MSC2 before the data phase.

For each of `command-timeout`, `data-crc` and `data-timeout`:

| Step | Check |
| --- | --- |
| One-shot read fault | the read succeeds after a retry; the fault hit count rises by one and the fault is consumed |
| Persistent read fault | the read returns `EIO` after more than one attempt |
| Clear | reads succeed again |
| Persistent write fault | the write returns `EIO` and the card's bytes are unchanged |
| One-shot write fault | the write succeeds after one retry, reads back, and reaches the card |

Before the faults the guest writes a file to the exFAT volume; after them it
mounts the volume again and reads the file back. After the emulator stops,
`fsck.exfat -n` must find the volume clean. The suite does not model a card's
flash translation layer, partial sector writes, wear or power loss.

## SPI NAND

The NAND is a generated DS35Q1GA image (`nand-ds35q1ga=on`: 1024 blocks of
64 pages of 2048 + 64 bytes), erased except for data pages in userdata
blocks 0, 1 and 3. The probe opens the production DT's `userdata` partition
(block 802 onward) through its MTD character device. Block numbers below are
relative to the partition. The faults are armed while the machine is paused
before its first instruction.

| Block | Fault | Expected with the fault | After `nand clear` |
| --- | --- | --- | --- |
| 0 | corrected ECC on page 0 | `EUCLEAN`, 4 corrected bitflips (the chip reports a 1 to 4 bucket), data intact; a raw read shows the flipped bit | clean read |
| 1 | uncorrectable ECC on page 0 | `EBADMSG`, one ECC failure | clean read |
| 2 | program failure on page 0 | write returns `EIO`; the page stays erased on the medium | write succeeds and reaches the medium |
| 3 | erase failure | erase returns `EIO`; the block keeps its data | erase succeeds |
| 4 | bad-block marker written on the medium | `MEMGETBADBLOCK` reports it bad | still bad |
| 5 | none | `MEMSETBADBLOCK` writes a marker the medium shows | still bad |
| 8, 9 | none | erase, program a position pattern, unaligned 1503-byte reads across page and block boundaries, erase block 8 again; the medium shows block 8 erased and block 9 programmed | — |

`ECCGETSTATS` must move by exactly the corrected and failed counts above.
The suite exercises the SPI NAND driver and MTD core. It does not cover UBI,
UBIFS or the skip-bad block driver's mapping of the root image; the
installed-image boot test (`os/install/test_boot.py --bad-blocks`) covers
that mapping.

## Watchdog

The emulator logs `x2000-wdt: watchdog reset` for each reset, so the suite
counts resets and fails on an unexpected one.

| Case | Check |
| --- | --- |
| Identity | `/sys/class/watchdog/watchdog0/identity` is `Ingenic X2000 watchdog`; the state after a cold boot is `inactive` |
| UAPI | keepalive, magic close and settable timeout are advertised; a second open is `EBUSY`; timeouts 0 and `max_timeout` + 1 are `EINVAL` and leave the timeout unchanged; a 2 s timeout reads back with 1 to 2 s left; disabling and then enabling the card, and the magic close, each outlive the timeout with no reset |
| Owner lost | a holder that feeds every 200 ms outlives one timeout with no reset; after `SIGKILL`, after `SIGSTOP`, and after a kernel panic with `kernel.panic=0`, the watchdog resets the machine and the next boot starts inactive |
| nowayout | with `ingenic_x2000_wdt.nowayout=1`, disabling the card is `EBUSY`, the magic close is accepted and the watchdog still resets the machine |

[`os/test_boot_watchdog.py`](../os/test_boot_watchdog.py) covers the early
boot owner and the descriptor transfer to the player supervisor on the rescue
image.
