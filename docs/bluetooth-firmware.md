# RTL8733BS firmware: sources and fetching

The device owner, 2026-11-21: "Is the bluetooth firmware available anywhere online that
we could consume it from? Please setup a fetch and extract process for it.
Please also research to see if updates are available for that or the wifi
firmware."

## Bluetooth

The Bluetooth branch uses the two files Shanling ships under `/firmware/rtlbt`:

| File | Bytes | SHA-256 | Contents |
| --- | --- | --- | --- |
| `rtl8733bs_fw` | 57,020 | `99603578f7f2b969e40a8156d1bdfd96f3418b4dc39770b6dc9a2eec1cb7bbe8` | "Realtech" epatch v1, `fw_version` `0xd5c81143`, project ID 19 (8723F family); chip ID 1 (ROM version 0) 12,337 bytes, chip ID 3 (ROM version 2) 44,469 bytes |
| `rtl8733bs_config` | 33 | `e1bd37cbbf18362e429b05b508a3341846127c51a11565d0514b977281087297` | magic `0x8723ab55`; entry `0x000c` (16 bytes, UART: rate code `0x04928001`, flow control), `0x008d` = `fa`, `0x008f` = `bf` |

Where it is published (checked 2026-11-21):

| Source | Result |
| --- | --- |
| linux-firmware (HEAD `a1ebb5ad7eae`) | No 8723F, 8723FS, 8733B or 8733BS file in `rtl_bt/` or `WHENCE`. |
| Linux `drivers/bluetooth/btrtl.c` (torvalds `c44104f3642d`, bluetooth-next `cf357cfc025e`) | No 8723F entry and no project ID 19. Out-of-tree patches add one that loads `rtl_bt/rtl8723fs_fw` and `rtl8723fs_config` (catplay-v851s `0001-btrtl-add-RTL8733BS-support.patch`, "Upstream-Status: Pending"; ivoh95/RTL8733BS_Bluetooth-linux). |
| Shanling Q2 V1.36 update ([download page](https://en.shanling.com/download/150), Google Drive ID `1HyST2-QkhUa6BdJP_ljBjkICxd-u8act`) | ZIP 50,293,829 bytes, SHA-256 `4e298cab6c12d86cfc55565645f7e3ae11d3fb44677a93ca13991e7184ecec4e`, dated 2026-11-12. `rtl8733bs_fw` and `rtl8733bs_config` byte-identical to V1.32. |
| Shanling Q2 V1.32 update | ZIP SHA-256 `154c1782…0f00`. No longer linked from the download page; the page offers V1.36 only. |
| [CoreELEC/rtkbt-firmware-aml](https://github.com/CoreELEC/rtkbt-firmware-aml) `b44fadaab4d7` (2026-07-15, "Update config/firmware for RTL8733BS") | `rtl8733bs_fw` 58,372 bytes, `45cc76eb84ae…70ec`, `fw_version` `0xd5d73187`: chip ID 1 patch identical to ours, chip ID 3 patch 45,821 bytes and different. `rtl8733bs_config` `efa8915db59c…f7a0` differs from ours in one byte: UART rate code `0x04928002` instead of `0x04928001`. |
| same repository `77ec2c6cbccc` (2025-08-14) | `rtl8733bs_8723fs_fw` 66,780 bytes, `fw_version` `0xd5c81140`, both patches different; 73-byte config. Replaced by the 2026 files. |
| radxa/rtkbt `72ef9b75374f` (2022-06-07) | `rtlbt/rtl8723fs_fw` 46,156 bytes, `fw_version` `0xdda9b625`, chip ID 1 identical, chip ID 3 different; config identical to CoreELEC's. `rtl_bt/rtl8723fs_fw` 53,052 bytes, both patches different. |
| ivoh95/RTL8733BS_Bluetooth-linux `70d73221ce0b` (2025-12-04) | `rtl8723fs_fw.bin` 40,404 bytes, `fw_version` `0xd59a28e9` (from gitlab.com/phodina/pinenote-firmware). |
| armbian/firmware, orangepi-xunlong/firmware, radxa/rkwifibt, firefly rkwifibt | No RTL8733BS or 8723F firmware. |

None matches our file exactly. None of these repositories has a licence for
the files; ivoh95's README calls them "Realtek proprietary". Allwinner Tina,
LibreELEC and Android device trees were not searched (GitHub code search
needs authentication).

The stock update is therefore the source. The files are not committed.

## Fetching

```sh
python3 tools/fetch_firmware.py                 # download V1.36 (pinned URL and SHA-256), extract, verify
python3 tools/fetch_firmware.py --offline       # only the cache or --zip; never the network
python3 tools/fetch_firmware.py --offline --source shanling-q2-v1.32 --zip 'build/firmware-dl/Q2 Firmware V1.32.zip'
python3 -m unittest tools/test_fetch_firmware.py
```

The tool downloads to (file name carries the pin), checks the ZIP's SHA-256,
reads `update.tar` and `recovery-update/rootfs.squashfs` from it in memory,
extracts only `/firmware/rtlbt/rtl8733bs_fw` and `rtl8733bs_config` with
`unsquashfs` (squashfs-tools from `PATH` or the rig SDK, or
`unsquashfs-backhand`), refuses symlinks and checks both pinned hashes. It
replaces only after every check passes and writes `provenance.json` beside the
files. A verified output is reused without opening the network. Nothing from the
archive is executed. Recorded in `c78594981` (2026-11-21).

The output directory is the input of the Bluetooth branch's
`os/install/build.py --bluetooth-firmware build/firmware/bluetooth`, of
`Q2_BT_FIRMWARE_DIR` for the `crates/bt` tests, and the files to copy to
`/state/bluetooth/firmware/` for a device probe. `BLUETOOTH_FIRMWARE` in
`tools/fetch_firmware.py` holds the same two hashes as that branch's
`build.py`; when the branches meet, `build.py` can import them from here.

The tests use a fake source: a ZIP built in the test, a fake `urlopen`, and a
stand-in extractor. They cover download and cache, offline refusal without a
cache, a local ZIP, a wrong download hash (no cache file left), an oversized
download, a corrupted cache, a changed, missing or symlinked firmware file
(the previous output kept), a rootfs that is not squashfs, and, when the rig
SDK's `mksquashfs` is present, a real squashfs through `unsquashfs`. With
`Q2_STOCK_ZIP` set to a stock ZIP, a further test extracts from it.

## Updates

**Bluetooth.** The CoreELEC file of 2026-07-15 is the newest published build
found. Its `fw_version` (`0xd5d73187`) is above ours (`0xd5c81143`) and above
CoreELEC's 2025 file (`0xd5c81140`), so the three look like one build series;
whether `fw_version` orders releases is unverified. No changelog exists. The
chip ID 1 patch is identical to ours, so the files differ only for a chip that
reports ROM version 2. The Q2's controller reports ROM version 2
([bluetooth.md](bluetooth.md#hardware-path)), so a candidate changes its patch.

```sh
python3 tools/fetch_firmware.py --candidate coreelec-2026-07-15
# -> build/firmware/bluetooth-candidate/coreelec-2026-07-15 (pinned per file)
```

The image build refuses a candidate, as its hashes differ.

**Wi-Fi.** The driver is Realtek `v5.15.17-113-g1924716b3.20250521_COEX20241204-390f`
([kernel/rtl8733bs](../kernel/rtl8733bs/README.md)) with embedded firmware
1.38.0 (built 2025-04-17). The same version is in CoreELEC/rtl8733BS
(`941029ad`, 2026-11-02), briansune/rtl8733BS_WiFi_linux (`0b81145e`,
2026-11-14, identical firmware arrays), libc0607/rtl8733bs-20250521 (identical
arrays) and Stane1983/rtl8733BS-aml. Older trees carry v5.14.1.1-46 (firmware
1.28.0, 2023) and v5.14.1.1-2 (firmware 1.22.0, 2022). No newer release was
found. Shanling's V1.36 `rtl8733bs.ko` (SHA-256 `61ae3a12…36c6`) has the same
version string as V1.32's (`0dd2c166…8b31`), was rebuilt on 2026-11-12, and
adds a `regulator_put` import; its `/firmware/rtlwifi` tables are unchanged.

## Testing a newer Bluetooth build on the Q2

The probe commands are in q2-player
[docs/bluetooth.md](https://github.com/q2-nova/q2-player/blob/main/docs/bluetooth.md#probe-commands).

1. Read the controller's ROM version with the stock files
   (`q2nd --bt-probe version --firmware /state/bluetooth/firmware`). If it is
   0, the candidate's patch for this chip is identical and there is nothing
   to test.
2. Copy the candidate to a separate directory, `/state/bluetooth/candidate/`,
   through `tests/perf/device_probe.py`. Do not touch `/lib/firmware/rtlbt`
   or `/state/bluetooth/firmware`.
3. Run `q2nd --bt-probe version --firmware /state/bluetooth/candidate` with
   the installed player paused. The patch is downloaded into the controller's
   RAM at every bring-up and is lost when BT_DIS_N drops, so turning Bluetooth
   off or rebooting undoes it; nothing on the Q2's flash changes.
4. The candidate config's UART rate code is `0x04928002` (1.5 M in Linux
   `btrtl`'s table) where the stock one is `0x04928001` (3 M). The host
   rewrites the configuration's UART entry to the rate it runs (3 M with
   RTS/CTS first). Pair the candidate patch with the stock config first,
   so only one thing changes, then try the candidate config.
5. If version, scan, pair and a stream pass with the candidate, compare
   stream dropouts and reconnects against the stock files over the same
   listening run before proposing a change. The shipped files stay as they
   are until the change is approved.
