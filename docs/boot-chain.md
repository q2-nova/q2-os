# Q2 boot chain, recovery paths and watchdog

The Q2 boots from SPI NAND through the X2000 boot ROM and a 16 KiB Ingenic
SPL. The SPL selects one of two kernel slots. Levels follow
[hardware.md](hardware.md#sources-and-levels). Device facts come from
read-only access under the stock kernel: partition reads through
`/dev/mtdNro`, `devmem` and `i2cget` reads, and logs.

## NAND part

The NAND is a Dosilicon **DS35Q1GA** (device-verified):

- READ ID returns E5h 71h; the read buffer repeats `e5:71:e5:71`. The
  DS35X1GA datasheet (Rev.03, Table 3.2) gives E5h 71h for DS35Q1GA.
- Linux reports an MTD master of `0x08000000` bytes (128 MiB) with 2048-byte
  pages, 64-byte OOB and 128 KiB eraseblocks: 1024 blocks in one plane.
- The flash map below ends at `0x7d00000` (125 MiB), inside 128 MiB.

The DS35Q2GA (E5h 72h, 256 MiB, two planes) is the default NAND in the QEMU
model and one entry in the stock kernel's Dosilicon parameter table
(`dosilicon_param`). The device does not report it. An emulated boot reads
E5h 72h unless the machine sets `nand-ds35q1ga=on`.

## Flash map

`/proc/mtd` and the partition table the SPL carries at flash offset 0x5800
agree (device-verified). The NAND reported 0 bad blocks.

| mtd | Name | Offset | Size | Contents |
| --- | --- | --- | --- | --- |
| 0 | uboot | 0x0000000 | 1 MiB | Ingenic SPL (16 KiB used; partition table at 0x5800) |
| 1 | kernel | 0x0100000 | 6 MiB | Main kernel, uImage, load and entry 0x80F00000 |
| 2 | rootfs | 0x0700000 | 75 MiB | Main squashfs |
| 3 | kernel2 | 0x5200000 | 6 MiB | Recovery kernel, uImage |
| 4 | rootfs2 | 0x5800000 | 12 MiB | Recovery squashfs: the `demo` updater UI |
| 5 | userid | 0x6400000 | 128 KiB | Device identity, read by stock `demo` through `mtd_debug` |
| 6 | ota | 0x6420000 | 128 KiB | Boot selector record |
| 7 | userdata | 0x6440000 | 24.75 MiB | UBI volume `user_data` |

The table at 0x5800 is the magic `nand`, a 32-bit entry count, then one entry
per partition: a 32-byte NUL-padded name and four little-endian 32-bit words
(size, offset, 0, flag). The flag is 0 on every partition except `userdata`,
where it is 1.

## Boot sequence

1. **Boot ROM** (16 KiB mask ROM at 0x1fc00000). The X2000 datasheet selects
   the boot source with `boot_sel[2:0]` on PE25 to PE27: SFC (NOR or NAND),
   MSC2 (SD) or USB. The ROM cannot be read once Linux runs: every word in
   0x1fc00000 to 0x1fc08000 reads `0x24010010` through `devmem`
   (device-verified).
2. **SPL**: "U-Boot SPL 2013.07-svn3071", 16 KiB (`INGE` header at 0x100,
   length word 0x4000). The ROM copies it to TCSM at 0x12401000 and enters it
   at 0xb2401800 on the ROM's stack. The SPL programs the PLLs (CPAPCR
   `0x03100869`, CPMPCR `0x07c04849`, CPCCR `0x9a094410`), brings up LPDDR3
   (`W63AH6NKB_BI`), reads the NAND ID and partition table, then loads and
   enters a kernel. There is no U-Boot proper, no environment, no console
   input and no SD or USB boot command (stock firmware: SPL disassembly).
3. **Kernel selection** (SPL function at 0xb240383c; stock firmware,
   emulator):
   - If GPIO 48 (PB16, the **Previous** key, active low) reads pressed, or
     the partition `ota` begins with `ota:kernel2`, the SPL prints
     `====into recovery====`, loads `kernel2` and passes
     `root=/dev/mtdblock_bbt_ro4` (rootfs2). The key read is
     `gpio_get_value(0x30)` at 0xb2403e04, which computes the pin register
     address (0xb0010000 + port × 0x100).
   - Otherwise it prints `====into normal====`, loads `kernel` and passes
     `root=/dev/mtdblock_bbt_ro2`.
   - The command line is a template whose `[mem-start…mem-end]` becomes
     `mem=128M@0x0`. The result matches the device's `/proc/cmdline`
     (device-verified):
     `console=ttyS3,115200n8 quiet mem=128M@0x0 init=/linuxrc flashtype=nand root=/dev/mtdblock_bbt_ro2 rootfstype=squashfs ro`.
4. **No integrity check and no automatic fallback.** The loader checks only
   the uImage magic `0x27051956`. With a bad magic it loads 384 KiB raw to
   0x80100000 and jumps there. There is no CRC check and no retry of the
   other slot. A corrupt main kernel hangs a normal boot; holding Previous at
   power-on still reaches the recovery slot (stock firmware, emulator).

### SPL peripheral setup

From disassembly of the SPL as a raw little-endian MIPS32r2 binary at
0xb2401000 (SHA-256
`68f46967d33346aec38297068fe7a9ada56679098871329168a6fc0da376e809`; stock
firmware):

- The early initializer (0xb2401ca4) calls 0xb2403e40, which sets function 0
  on port E mask 0x003f0000 (PE16 to PE21, SFC) and port C mask 0x06000000
  (PC25 and PC26, UART3) through the GPIO helper at 0xb2403d80. These are the
  helper's only call sites.
- The SPL does not configure PC23 and PC24 (PMIC GPIO I²C) or the DAC
  control pins on port B.
- The main path sets clocks, OST, DDR and SFC. The DDR hook table at
  0xb2404ba0 holds a no-op (0xb240259c) and a null entry.
- The SPL writes no PMIC registers and runs no DAC power sequence. What the
  boot ROM or the PMIC's nonvolatile defaults set before the SPL is not
  known.

## Stock update and recovery flow

From both stock `demo` binaries (stock firmware; the recovery path also
emulated):

- **Main system** (`/release/bin/demo`): stages the update's `xImage` and
  `rootfs.squashfs` in `/mnt/mmc/recovery-update/`, runs
  `flash_erase /dev/mtd6 0 1` and
  `printf "%-256s" "ota:kernel2" | nandwrite -s 0 -p /dev/mtd6 -`, then
  reboots.
- **Recovery system** (rootfs2 `demo`): `flash_erase /dev/mtd1 0 0`,
  `nandwrite -m -p /dev/mtd1 xImage`, the same for mtd2 with the squashfs,
  `rm -rf /mnt/mmc/recovery-update`, `flash_erase /dev/mtd6 0 1`, reboot.
- Without `recovery-update/` on the card, the recovery system erases the
  `ota` block and reboots.

The flow never writes kernel2 or rootfs2. While `ota` holds the record, every
boot returns to recovery, so an interrupted write of the main slots is
retried at the next boot. Recovery depends on the SPL block, the recovery
slot and a card the recovery kernel can read. Writing mtd0, mtd3 or mtd4 is
unrecoverable without boot ROM USB access.

## Recovery paths

| Path | Level | Notes |
| --- | --- | --- |
| Power-key force-off | device-verified | AXP2101 `0x22 = 0x02` (button power-off enabled, off not restart), `0x27 = 0x1d` (10 s off, 512 ms on). `0x21 = 0x01` after a power-key shutdown. A hardware power cut independent of software |
| Previous held at power-on | stock firmware, emulator | Boots kernel2 and rootfs2 without a working main system. With Shanling's `recovery-update/` on the card it rewrites mtd1 and mtd2. The Q2 Pod project documents the same procedure |
| `ota` record | stock firmware, emulator | `ota:kernel2` boots the recovery slot; written by the stock updater |
| Stock firmware update from the card | stock firmware | Uses the `ota` path; rewrites mtd1 and mtd2 only |
| Boot ROM USB boot | unverified | Ingenic boot ROMs fall back to USB boot when no valid SPL is found. Whether any Q2 button drives a `boot_sel` pad is not known; button trials with a USB monitor attached showed no USB boot device |
| UART console | device-verified | `ttyS3` (UART3, PC25 and PC26). The pads are inside the case |
| kexec from stock | stock firmware | Unavailable: the stock kernel's `kexec_load` is `sys_ni_syscall` |

## Emulated boot from the SPL

The QEMU `q2` machine can start from the SPL in a NAND image
(`-M q2,boot-source=nand`) instead of a `-kernel` image; see the
[QEMU board documentation](https://github.com/q2-nova/qemu/blob/q2/10.1/docs/system/mips/shanling-q2.rst).
Its boot ROM stand-in copies the SPL (length from its header) to 0x12401000
and starts CPU0 at 0xb2401800 with SP 0xb2401000. The ROM area holds zeros;
the SPL reads word 0x84 only to choose its version banner, so the emulated
SPL prints "Current Version: V1".

With a full-chip image in the device's layout (emulator):

| Case | Result |
| --- | --- |
| Previous, Down, Up, Power or no key held at boot | Recovery only with Previous |
| Previous held, stock V1.32 `recovery-update/` on the card | Recovery rewrites mtd1 (42 blocks) and mtd2 (385 blocks), erases `ota` and reboots; with the key still held it enters recovery again and only erases `ota`; then a normal boot. mtd1 and mtd2 match the package; the SPL block and kernel2 are unchanged |
| First 128 KiB of mtd1 zeroed, no key | Hangs after `====into normal====` |
| Same corrupt image, Previous held | Recovery restores mtd1 and mtd2; the normal boot reaches the stock player |

The emulated stock kernel logs `cma: Failed to reserve 128 MiB` and
`Failed to initialize '/core-ost@0x12000000'`; whether the device logs them
is not checked.

## Watchdog

The X2000 watchdog is the TCU's WDT (TDR, TCER, TCNT, TCSR at 0x10002000), a
16-bit up-counter that resets the SoC on compare.

| Fact | Source | Level |
| --- | --- | --- |
| Stock `reboot` is a watchdog reset: `_machine_restart` is `jz_wdt_restart`, which writes `REBOOT_SIGNATURE` 0x3535 to CPM scratch and arms a 4 ms count | stock kernel symbols (`jz_hibernate_restart` is absent) | stock firmware |
| CPM `RSR` (0x10000008): bit 0 power-on, bit 1 watchdog, bit 2 P0 or hibernate. A power-key cycle leaves 0x1; a manual power-on has read 0x5 | Ingenic SDK `cpm.h`; register reads | device-verified |
| TCSR selects PCLK, RTC or EXTCLK with a /1 to /1024 prescaler. `OPCR.ERCS` (bit 2) chooses the RTC clock: the 32.768 kHz crystal or EXCLK/512 (46.875 kHz) | Ingenic SDK | vendor source |
| The vendor `wdt_start_count` clears ERCS, against its own comment. The Linux driver takes a 32,768 Hz RTC clock from the device tree and does not read ERCS. With EXCLK/512 selected, Linux timeouts would run about 30 % short | Ingenic SDK; Linux watchdog driver | vendor source; OPCR not read on the device |
| A watchdog reset restarts the SoC only; the PMIC, SD card, Wi-Fi module and codecs are not power-cycled. An orderly `reboot` runs shutdown scripts and driver shutdown first; an expiry does not | SoC reset design | unverified |

## Stock kernel properties

The stock kernel loads unsigned modules (`modules_disabled=0`, no module
signatures, vermagic `5.10.21+ SMP preempt mod_unload MIPS32_R5 32BIT`),
exposes `/proc/kallsyms` addresses to root, and builds most drivers (audio,
display, Wi-Fi, MMC, watchdog) as modules (device-verified).
