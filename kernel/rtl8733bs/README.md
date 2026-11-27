# RTL8733BS Wi-Fi driver for the mainline Q2 kernel

Current qualification correction (2026-11-03): the baseline now has physical
WPA2 association/streaming and normal installed-player evidence; the older
initial status below is historical. WPA3 and AP/station concurrency remain
separate physical gates. See [the priority handoff](../../docs/mainline-stock-priority-qualification.md).

Optional patch 0004 normalizes cfg80211 external-auth fields instead of casting
its nullable PMKID pointer to the vendor's inline array. Enable only with
`prepare.py --sae-external-auth`; ordinary preparation deliberately retains the
three-patch baseline. Native and actual MIPS tests extract the real function
and actual kernel/vendor layouts, checking all fields, 32-byte SSID, exact
PMKID and NULL. The legacy array branch is a compatibility fixture, not an
old-kernel hardware result:

```sh
python3 kernel/rtl8733bs/test-external-auth.py --kernel-source build/linux/src \
  --driver-source build/rtl8733bs/sae-src --sdk /path/to/generated/host \
  --qemu /path/to/qemu-mipsel-static
```

The optional module builds against V0.16's exact config/export ABI, but is not
installed or radio-qualified. Keep ordinary V0.16 driver bytes unchanged until
the primary qualifies and explicitly promotes it. No numbered in-tree kernel
patch or firmware-array change is involved.

The Q2's Wi-Fi is a Realtek RTL8733BS (Wi-Fi 4 and Bluetooth combo) on the
X2000's MSC1 SDIO controller. Linux 7.3 has no driver for it: rtw88 and rtw89
do not list the chip. This directory ports Realtek's own SDIO driver, the
release the stock firmware runs, to the patched Linux 7.3 kernel in
`kernel/patches/`. It builds as an out-of-tree module, `8733bs.ko`.

**Status:** builds against the Q2 kernel, loads and unloads in the emulator,
and the board wiring (patch 34) powers the slot in the stock order. The
emulator has no RTL8733BS model, so chip probe, firmware download, scanning,
association, power saving and throughput are untested. Nothing has run on a
physical Q2.

## Source and provenance

| Item | Value |
| --- | --- |
| Driver | Realtek `v5.15.17-113-g1924716b3.20250521_COEX20241204-390f`, GPL-2.0 |
| Pinned copy | [smp79/rtl8733BS_WiFi_linux_v5.15.17-113](https://github.com/smp79/rtl8733BS_WiFi_linux_v5.15.17-113) commit `6b522b308fb7482cb43be290d639cfdf2bfd1838` |
| Tree digest | `1fa8efe2f4121a88ef6d148bd892738a039e2d419d5ed22707babfd91a0c5015` (sorted paths, exec bits, file SHA-256s) |
| Stock module | V1.32 `/module_driver/rtl8733bs.ko`, SHA-256 `0dd2c166…8b31`, same `DRIVERVERSION`, built from Ingenic's `rtl8733BS_WiFi_linux_v5.10_wifibt` tree |

Evidence that this is the stock release: the stock module's version string is
identical, and both normal-mode firmware images embedded in
`hal/rtl8733b/hal8733b_fw.c` (`array_mp_8733b_fw_nic`, 126,664 bytes, and
`ccv_array_mp_8733b_fw_nic`, 127,064 bytes) occur byte-for-byte in the stock
module. The stock module does not contain the WoWLAN images; it was built
without `CONFIG_WOWLAN`. The vendor SDK in `DiamondBond/q2-kernel` does not
include this driver; its `module_drivers` tree has only other Realtek chips.

Other copies of this release were compared. `Stane1983/rtl8733BS-aml` differs
only by Linux 6.9/6.12 cfg80211 compatibility helpers, which patch 3 reuses
with credit. `libc0607/rtl8733bs-20250521` carries FPV-specific changes and
was not used. `Charliechen114514/rtl8733bu-linux-driver` (USB variant, Linux
7.1) was a reference for the API port; its code was not copied.

No newer release of this driver or its firmware (1.38.0) was found on
2026-11-21; Shanling's V1.36 update carries the same version
([firmware sources](../../docs/bluetooth-firmware.md#updates)).

## What the port changes

`patches/` applies on the pinned commit:

1. **Build for the Shanling Q2.** A Q2 platform block: little-endian MIPS,
   cfg80211 with station events, concurrent mode, PHY parameter files from
   `/lib/firmware/rtlwifi/`. Linux 6.15 removed kbuild's `EXTRA_CFLAGS`, so
   the flags pass through `ccflags-y`. Options follow the stock module's
   `dump_drv_cfg` strings, which now match exactly: power saving on
   (IPS, LPS, LPS_LCLK, CPWM polling, LPS_ACK), MBO/802.11k/v/r off. Bridge
   NAT (`CONFIG_BR_EXT`) is off: the player never bridges `wlan0`, and its
   PPPoE parsing uses struct fields Linux hides from kernel builds.
2. **Kernel APIs through 7.3.** Timer renames (6.2, 6.16), string
   `MODULE_IMPORT_NS` (6.13), removed `strncpy` (7.2; a local copy keeps its
   semantics), and a rename of the driver's `hmac_sha256`, which collides
   with the kernel's.
3. **cfg80211 through 7.3.** Channel-switch and CAC changes (6.9, 6.12),
   `set_monitor_channel` (6.13), `get_tx_power` link and radio indices (6.14,
   6.17), `set_wiphy_params`/`set_tx_power` radio index (6.17), key and station
   operations and events on `wireless_dev` (7.1), and caller-assigned cookies
   for `remain_on_channel`/`mgmt_tx` (7.3). Thin forwarders keep the vendor
   operations unchanged.

Every version gate was checked against that release's tagged header
(`include/net/cfg80211.h`, `include/linux/timer.h`, `string.h`, `module.h`).

The stock module's Ingenic glue (`rtk_wlan_power_on`, `jzmmc_manual_detect`,
module parameters `gpio_wl_dis_n`/`gpio_bt_dis_n`) is not ported. The device
tree supplies the same power sequence; see below. After the port, the
remaining defined-symbol differences from the stock module are that glue,
NAT25, the new forwarders and compiler inlining.

## Board wiring (kernel patch 34)

Recovered from the stock module's disassembly and `/module_driver/*.sh`:

| Signal | Pin | Stock behaviour |
| --- | --- | --- |
| BT_DIS_N | PD20 | Driven high first, by the shared Wi-Fi/BT power-on |
| WL_DIS_N | PD01 | Driven high 10 ms later, then MSC1 is rescanned; after dropping it, 100 ms wait |
| SDIO | MSC1, 0x13460000, IRQ 36, PD08–PD13 | Non-removable, 4-bit, keep power in suspend, up to 100 MHz |

Both enables are active high (`rtk_wlan_power_ctrl` passes `on != 0` straight
to the pin). Patch 34 models them as two fixed regulators: WL_DIS_N is MSC1's
`vmmc-supply` and is supplied by BT_DIS_N, which has a 10 ms startup delay;
WL_DIS_N has a 100 ms minimum off time. `mmc-pwrseq-simple` is not used: with
a single reset GPIO, Linux 7.3 takes a shared reset control whose
pre-power-on deassert/assert pulses WL_DIS_N high for microseconds before
every power-up. The emulator showed that pulse; stock never produces it.

MSC1 runs at 50 MHz high speed with 3.3 V signalling, below stock's 100 MHz,
until a hardware test. Stock uses in-band SDIO card interrupts: on the device,
MSC1 interrupts follow packets (about 8.5 per frame, experiments 3 and 21 in
[power-experiments.md](../../docs/power-experiments.md)),
and stock `soc_msc.ko` registers through the kernel's own `sdhci_add_host`,
which enables them. Mainline SDHCI does the same by default.

## Build

```sh
KSRC=build/linux/q2-src kernel/prepare.sh             # Linux 7.3 + patches incl. 34
KSRC=build/linux/q2-src KOUT=build/linux/q2-out kernel/build.sh
python3 kernel/rtl8733bs/prepare.py [--stock-ko PATH/rtl8733bs.ko]
KOUT=build/linux/q2-out kernel/rtl8733bs/build.sh   # -> build/rtl8733bs/src/8733bs.ko
```

`prepare.py` downloads the pinned commit once (cached under `build/rtl8733bs/`),
checks the tree digest, version and firmware hashes, and applies the patches
with `git am`, so the tree can be edited and re-exported with
`git format-patch`. `--stock-ko` also checks the firmware against a stock
module. The module must be built against the same `KOUT` as the kernel it
loads into. The kernel needs `CONFIG_CFG80211` (patch 34 enables it).

Install `8733bs.ko` with the kernel's modules. The driver loads
`PHY_REG_PG.txt` and `TXPWR_LMT.txt` from `/lib/firmware/rtlwifi/` if present
and otherwise uses built-in tables. Stock firmware ships both files, but
they are not needed: the stock `PHY_REG_PG.txt` (labelled `rtl8723f
version = 33`) has the same per-rate powers as the built-in table, and both
builds disable power-limit enforcement (`CONFIG_TXPWR_LIMIT_EN=0`), so
`TXPWR_LMT.txt` is not applied.

## Emulator check

```sh
python3 kernel/wifi.py --kernel KOUT/uImage.gz --module build/rtl8733bs/src/8733bs.ko [--icount]
```

The kernel needs the fixture initramfs embedded (`kernel/mkinitramfs.sh`,
then `INITRAMFS=build/linux/initramfs.cpio kernel/build.sh`). The test writes
the module to a generated SD image, copies it out in the guest and checks its
hash. It checks MSC1 as `mmc1` with the microSD still `mmcblk0`, the
regulator chain and pin claims, the power sequence from QEMU's `wifi` log
(BT_DIS_N, then WL_DIS_N at least 10 ms later; reverse on power-off; WL_DIS_N
never high while BT_DIS_N is low; at least 100 ms off), and two
load/unload cycles without kernel warnings.

Results on 2026-11-01 with kernel `b2766ca6…`, module `b69c124e…`, QEMU
`846c059b…`: normal `build/linux/q2-wifi-185b8defad`, icount
`build/linux/q2-wifi-64cf6db818`, both passed (WL_DIS_N 12.8/12.5 ms after
BT_DIS_N, four power cycles each). The existing peripheral smoke test also
passes on this kernel (`q2-mainline-448192db27`, icount
`q2-mainline-d3bb606fd6`). After rebasing onto master's SPL-boot emulator
changes, QEMU `d2da0789…` passes the same kernel and module (normal
`q2-wifi-e4b2584534`, icount `q2-wifi-c2d2ac759f`). Delays are virtual-time
emulator observations, not hardware measurements.

## Not yet established

- **Chip bring-up.** Probe reads the chip over SDIO, powers it through
  halmac and downloads firmware. None of this can run without the chip or a
  model of it ([qemu.md](../../docs/qemu.md#long-tail) lists that model as
  deferred). The first physical test is the first real probe.
- **Signalling and clock.** MSC1's I/O voltage is unknown; 3.3 V, 50 MHz is
  a conservative choice to revisit on hardware.
- **Power.** Power saving matches stock, including the legacy packet-count
  LPS rules ([power.md](../../docs/power.md#wi-fi-power-save-the-drivers-rules));
  `CONFIG_LPS_CHK_BY_TP` is off in both. Those rules keep the chip out of LPS
  while streaming radio. SDIO runtime PM, suspend with
  `keep-power-in-suspend`, and Wi-Fi host-wake are not wired; stock has no
  wake GPIO configured either.
- **Bluetooth.** BT_DIS_N rises with Wi-Fi, as on stock. No Bluetooth driver
  is integrated; a later one can share the `wlan-bt-en` supply.
- **OS integration.** The optional source-built diagnostic now embeds the
  module and dependencies for manual post-boot loading: see
  [hardware OS updates](../../docs/hardware-os-updates.md). Network association
  tooling (wpa_supplicant/iw) and physical connection tests remain separate.
- **MAC address** comes from the chip's efuse; `/data/wifimac.txt`, which the
  driver checks first, does not exist on stock either.

## Preserving the installed suspend policy (2026-11-06)

The older bring-up coverage above is historical. Current hardware/network and
power investigation is recorded in [wifi-cold-suspend.md](../../docs/validation/wifi-cold-suspend.md).
Patch0005 now carries forward the installed bounded IPS-worker wait and safe
retention-refusal ordering; default preparation must retain those fixes. Cold
SDIO suspend remains an experimental --cold-suspend opt-in through patch0006.
Both variants replay the pinned source and retain optional SAE as a separate
choice. Do not replace the installed policy with the earlier clean prototype.

For the current experimental PM profile, follow AGENTS.md and use the maintained
verified module builder with the actual qualified frozen kernel source/config/
exports, two jobs and frozen outputs. The legacy commands above do not qualify
an alternative ABI-compatible build tree. The installed-module comparison tool
checks real code/data bytes and can freeze public source; that comparison alone
is not a fresh-build or kernel-ABI qualification.

## Optional cached power-state diagnostic (2026-11-07)

`prepare.py --cached-power-state` additionally applies patch0007. Ordinary
preparation leaves it out. It adds a nonblocking, numeric cached snapshot to
`ps_info`, without register/firmware queries or policy changes. Busy-lock and
feature-unavailable coverage is explicit; CPWM is a software shadow. It exposes
group-key presence only, never the key or owner configuration.

For the current investigation, `prepare_cached_ps.py` reproduces the exact
qualified frozen cold-source bytes and proves that only the helper/getter hook
changes. The native fixture compiles the actual helper and vendor timer accessor,
not a substitute timer type. Build the module through the maintained verified
module builder against the exact frozen kernel closure. This diagnostic does
not qualify a production policy change; physical rollback/coverage remains a
separate gate. [Inputs, actual-code controls and batch contract](../../docs/validation/rf-cached-ps-state.md).


## Current optional RF diagnostics and rejected recovery candidate

Patch0008 adds non-atomic cached data-path observations to the exact qualified
cold diagnostic source; `prepare_cached_datapath.py` keeps default preparation
unchanged. [Physical data and interpretation](../../docs/validation/rf-cached-datapath.md).

Patch0009 and `prepare_cold_association.py` are an **unpromoted experimental
candidate**, not a qualified RF repair. Actual-C/native/MIPS controls pass, but
the first physical5s CORE/full return still loses data. The normal installed
V043 driver is restored. Do not add0009 to default preparation on the strength
of source/native checks or successful cleanup receipts. Preserve the failed
candidate and [physical qualification](../../docs/validation/rf-cold-disconnect.md).
