# RTL8733BS Wi-Fi driver for the mainline Q2 kernel

The Q2's Wi-Fi is a Realtek RTL8733BS (Wi-Fi 4 and Bluetooth combo) on the
X2000's MSC1 SDIO controller. Linux 7.3 has no driver for it: rtw88 and rtw89
do not list the chip. This directory ports Realtek's own SDIO driver, the
release the stock firmware runs, to the Q2's Linux 7.3 kernel
([q2-nova/linux](https://github.com/q2-nova/linux), the revision
`linux_production` in `os/sources.json`). It builds as an out-of-tree module,
`8733bs.ko`.

**Status:** the default patch set associates and streams over WPA2 on a
physical Q2 in the installed player. WPA3 and AP/station concurrency are not
qualified. The emulator has no RTL8733BS model, so chip probe, firmware
download, association and power saving run only on hardware.

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

Shanling's V1.36 update carries the same driver and firmware version
([firmware sources](../../docs/bluetooth-firmware.md#updates)).

## What the port changes

`prepare.py` applies `patches/` in order on the pinned commit. Patches 0004
and 0006–0009 are opt-in; every other patch is in the default set.

1. **Build for the Shanling Q2.** A Q2 platform block: little-endian MIPS,
   cfg80211 with station events, concurrent mode, PHY parameter files from
   `/lib/firmware/rtlwifi/`. Linux 6.15 removed kbuild's `EXTRA_CFLAGS`, so
   the flags pass through `ccflags-y`. Options follow the stock module's
   `dump_drv_cfg` strings, which match exactly: power saving on
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

The remaining default patches: 0005 bounds the IPS-worker wait during suspend
and refuses a failed retention admission before quiescing the radio; 0010
adds a live LPS slow-transition parameter; 0011 skips HALMAC message
formatting when debug output is compiled out; 0012 adds live switches for
SDIO wakeup sources; 0013 builds without MP and P2P, at `-Os`, with eight TX
buffers; 0014 drops the second Wi-Fi interface by default; 0015 adds a
`CONFIG_LPS_PG` build option. Each patch's message gives its reasons.

The stock module's Ingenic glue (`rtk_wlan_power_on`, `jzmmc_manual_detect`,
module parameters `gpio_wl_dis_n`/`gpio_bt_dis_n`) is not ported. The device
tree supplies the same power sequence; see below. After the port, the
remaining defined-symbol differences from the stock module are that glue,
NAT25, the new forwarders and compiler inlining.

## Board wiring

Recovered from the stock module's disassembly and `/module_driver/*.sh`:

| Signal | Pin | Stock behaviour |
| --- | --- | --- |
| BT_DIS_N | PD20 | Driven high first, by the shared Wi-Fi/BT power-on |
| WL_DIS_N | PD01 | Driven high 10 ms later, then MSC1 is rescanned; after dropping it, 100 ms wait |
| SDIO | MSC1, 0x13460000, IRQ 36, PD08–PD13 | Non-removable, 4-bit, keep power in suspend, up to 100 MHz |

Both enables are active high (`rtk_wlan_power_ctrl` passes `on != 0` straight
to the pin). The Q2 device tree in the linux fork
(`arch/mips/boot/dts/ingenic/shanling-q2.dts`) models them as two fixed
regulators: WL_DIS_N is MSC1's `vmmc-supply` and is supplied by BT_DIS_N,
which has a 10 ms startup delay; WL_DIS_N has a 100 ms minimum off time.
`mmc-pwrseq-simple` is not used: with a single reset GPIO, Linux 7.3 takes a
shared reset control whose pre-power-on deassert/assert pulses WL_DIS_N high
for microseconds before every power-up, which stock never does.

MSC1 runs at 50 MHz high speed with 3.3 V signalling, below stock's 100 MHz.
Stock uses in-band SDIO card interrupts, and stock `soc_msc.ko` registers
through the kernel's own `sdhci_add_host`, which enables them. Mainline SDHCI
does the same by default.

## Build

The installed-image kernel build (`os/install/kernel_build.py`, for a kit with
stock Wi-Fi) prepares this driver with the default patch set, builds it
against the production kernel and installs it as `extra/8733bs.ko`. By hand:

```sh
python3 kernel/rtl8733bs/prepare.py [--stock-ko PATH/rtl8733bs.ko]
KOUT=build/linux/out kernel/rtl8733bs/build.sh   # -> build/rtl8733bs/src/8733bs.ko
```

`prepare.py` downloads the pinned commit once (cached under `build/rtl8733bs/`),
checks the tree digest, version and firmware hashes, and applies the patches
with `git am`, so the tree can be edited and re-exported with
`git format-patch`. `--stock-ko` also checks the firmware against a stock
module. `KOUT` is a configured Linux build directory with its modules built;
the module must be built against the kernel it loads into, which needs
`CONFIG_CFG80211`.

Install `8733bs.ko` with the kernel's modules. The driver loads
`PHY_REG_PG.txt` and `TXPWR_LMT.txt` from `/lib/firmware/rtlwifi/` if present
and otherwise uses built-in tables. Stock firmware ships both files, but
they are not needed: the stock `PHY_REG_PG.txt` (labelled `rtl8723f
version = 33`) has the same per-rate powers as the built-in table, and both
builds disable power-limit enforcement (`CONFIG_TXPWR_LIMIT_EN=0`), so
`TXPWR_LMT.txt` is not applied.

## Optional patches

| Patch | `prepare.py` option | Change |
| --- | --- | --- |
| 0004 | `--sae-external-auth` | Normalizes cfg80211 external-auth fields instead of casting the nullable PMKID pointer to the vendor's inline array |
| 0006 | `--cold-suspend` | Cold SDIO suspend unless a wake source is enabled |
| 0007 | `--cached-power-state` | A nonblocking, numeric cached power-state line in `ps_info`: no register or firmware queries, no policy change; group-key presence only, never the key |
| 0008 | `--cached-datapath` (with 0007) | Non-atomic cached data-path counters in `ps_info`; HIMR/HISR are software copies |
| 0009 | `--cold-disconnect` | Reports a disconnect to cfg80211 before cold firmware teardown; firmware-retaining WoW paths keep their roaming behaviour |

None of the optional patches is qualified for the installed image. Patch 0009
passes its native checks, but a physical return from cold suspend with it
still loses data, so it stays out of the default set.

## Native function tests

Each test extracts the actual C functions from a driver tree that
`driver_source.py` prepares with `prepare.py` (once per patch selection, under
`build/rtl8733bs/test-sources/`), compiles them with the host `cc` against
generated stimuli, and checks every output. Negative variants of the same
code must fail. They cover C behaviour only, not RF, firmware or the MIPS ABI.

| Test | Driver tree | Checks |
| --- | --- | --- |
| `test_ips_wait.py` | cold suspend against the default set | The IPS-worker wait in `rtw_suspend_common` is bounded across release times and timer wrap, and is identical in both trees |
| `test_suspend_power.py` | cold suspend | SDIO suspend/resume callbacks: cold cycles, inactive card, IRQ ownership, error rollback, wake modes |
| `test_cached_ps.py`, `test_cached_ps_controls.py` | 0006 + 0007 | Formatter fields, feature branches, try-lock and noninterference; the patch adds only the formatter and its hook |
| `test_cached_datapath.py`, `test_cached_datapath_controls.py` | 0006–0008 | Counter widths, try-lock and noninterference; the patch adds only the formatter and its hook |
| `test_cold_association.py`, `test_cold_association_controls.py` | 0006–0009 against 0006–0008 | Cold teardown clears roaming before every callback; WoW paths equal the predecessor |
| `test-external-auth.py` | 0004 | The external-auth normalization against the real cfg80211 and vendor layouts; `--sdk` and `--qemu` repeat it for MIPS o32 |

```sh
python3 kernel/rtl8733bs/test_cached_ps_controls.py
python3 kernel/rtl8733bs/test-external-auth.py --kernel-source LINUX_SOURCE \
  [--sdk SDK_HOST_DIR --qemu QEMU_MIPSEL_USER]
```

`LINUX_SOURCE` is a production kernel tree from
`python3 os/install/prepare.py --dest build/...`. The CLI tests accept
`--source` (and `--control` or `--baseline`) to test another driver tree.

## Not established

- **Signalling and clock.** MSC1's I/O voltage is unknown; 3.3 V, 50 MHz is
  a conservative choice to revisit on hardware.
- **Power.** Power saving matches stock, including the legacy packet-count
  LPS rules ([stock-firmware.md](../../docs/stock-firmware.md#power-save-lps-rules));
  `CONFIG_LPS_CHK_BY_TP` is off in both. Those rules keep the chip out of LPS
  while streaming radio. SDIO runtime PM and Wi-Fi host-wake are not wired;
  stock has no wake GPIO configured either.
- **Bluetooth.** BT_DIS_N rises with Wi-Fi, as on stock. A Bluetooth driver
  can share the `wlan-bt-en` supply.
- **MAC address** comes from the chip's efuse; `/data/wifimac.txt`, which the
  driver checks first, does not exist on stock either.
