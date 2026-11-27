# Stock firmware behaviour

What Shanling's stock firmware does with the Q2's hardware. Unless a line
says otherwise, the facts come from static analysis of the V1.32 package
(kernel image with its device tree and symbols, `/module_driver`, loader
scripts, `release/bin/demo`) or from the Rockbox port and Q2 Pod notes, which
use the stock kernel. Levels follow
[hardware.md](hardware.md#sources-and-levels); the hardware itself is
described there, and the boot and update paths are in
[boot-chain.md](boot-chain.md).

## System

| Fact | Source | Level |
| --- | --- | --- |
| Buildroot 2020.02.1 userspace; Linux `5.10.21+` SMP PREEMPT, `mips` | `/etc/os-release`, `uname` | device-verified |
| `/proc/device-tree/model` does not exist; the root `compatible` is only `ingenic,x2000` | procfs | device-verified |
| The microSD card is `/dev/mmcblk4p1`, mounted on `/mnt/mmc`; the card is ready once `/tmp/mmc_add` exists | mounts; Rockbox | device-verified |
| `S90play` starts `/release/bin/demo &`. `S11logo_display_shell` waits for `/dev/fb0` and runs `display_logo` on the boot logo | init scripts; Rockbox | stock firmware |
| The firmware version is a literal in `demo` and in `firmware_v20.info`. The root is squashfs (LZO, 128 KiB blocks) and an update must not exceed the stock root's size | Rockbox; Q2 Pod | stock firmware |
| The kernel config is not exposed (`/proc/config.gz` absent) | procfs | device-verified |

## Display, backlight and keys

| Fact | Source | Level |
| --- | --- | --- |
| Two framebuffer pages, flipped with `FBIOPAN_DISPLAY` then `FBIO_WAITFORVSYNC`; red at bit 16 | Rockbox | device-verified |
| Backlight through `/dev/jz_pwm`: request "PC00", config {1, 0, 0, 10000, 111, channel}, level = brightness + 10 | Rockbox | device-verified |
| Screen off: PWM 0, wait 50 ms, `FBIOBLANK` 4. Screen on: `FBIOBLANK` 0, pan, wait 50 ms, PWM level | Rockbox | device-verified |
| A new process inherits the framebuffer blank state of the last one. On a warm start `PWM_REQUEST` returns channel 0, `PWM_CONFIG` returns EPERM, and `PWM_LEVEL` on that channel still succeeds. The first pan after a blank can return EBUSY | ioctl traces | device-verified |
| `/sys/class/graphics/fb0/blank` has no readable state | sysfs | device-verified |
| Keys come from the evdev device `md-gpio-keys`; Bluetooth headset buttons appear as "NAME (AVRCP)" while connected | Rockbox | device-verified |
| Jacks are read with the `/dev/gpio` ioctl `0x2000477a` on "PA07" and "PA08"; 0 is plugged. The codec module has no jack-detect code | Rockbox; `codec_cs43131.ko` | device-verified |
| evdev accepts injected events only in whole 16-byte `input_event` units per `write()` | event injection | device-verified |

## Audio

| Fact | Source | Level |
| --- | --- | --- |
| `/dev/shanling_dac` ioctls: headset 0xc0044d1c, power 0xc0044d1a, PCM 0xc0044d1b, volume 0xc0044d00 (L << 8 \| R, 0 to 100), mute 0xc0044d1f, gain 0xc0044d0d, filter 0xc0044d19. PCM is `plughw:0,0` | Rockbox | device-verified |
| `cs43131_dac_init.part.0` writes both init tables (`.rodata+0x4f8`, `+0x600`) to all four codecs: CLASS_H_CTL 0x1E (adapt, `HV_EN` 1), HP_OUT_CTL_1 0x31 (2 Vrms) | `codec_cs43131.ko` | stock firmware |
| The gain setting is digital: High reads `high_volume_table`; Low reads `low_volume_table` and adds 12 half-dB steps of attenuation. Neither writes the analog registers. The high branch raises PB02 at volume 90 and above | `dac_set_volume.part.0` | stock firmware |
| `soc_aic.ko` takes an unbalanced clock-gate reference in startup and shutdown: each PCM open and close with no writes raises the `gate_i2s1` enable count by one (36, 37, 38, 39) while ALSA reports `closed` | disassembly of the installed module; three open and close cycles | device-verified |
| `uac_device_config.sh` creates a UAC2 gadget: 48 kHz, 32-bit, stereo playback and capture, VID 0x20B1, PID 0x301F. The player also reads `/sys/class/uac2/uac2_samprate` and writes `uac2_outputway`, which are vendor additions | scripts; `demo` | stock firmware |
| Bluetooth audio uses bluez-alsa's `bluealsa` PCM | Rockbox | device-verified |
| AirPlay: `shairport -a NAME -o alsa -M /tmp/dirairplay -d`. The receiver writes `key=value` blocks to a FIFO `now_playing`, opened non-blocking, so a reader must hold it open | `demo` | stock firmware |

## Wi-Fi

| Fact | Source | Level |
| --- | --- | --- |
| `rtl8733bs.ko` (module `8733bs`) loads at boot with `gpio_wl_dis_n=PD01 gpio_bt_dis_n=PD20` | `rtl8733bs.sh` | stock firmware |
| Driver release `v5.15.17-113-g1924716b3.20250521`, from Ingenic's `rtl8733BS_WiFi_linux_v5.10_wifibt` tree. Both normal-mode firmware images match Realtek's published source byte for byte; WoWLAN is not built. Options: cfg80211, concurrent mode, IPS and LPS power saving, PHY files from `/firmware/rtlwifi/`, no MBO or 802.11r | `dump_drv_cfg` strings; firmware comparison | stock firmware |
| `PHY_REG_PG.txt` (labelled `rtl8723f version = 33`) equals the built-in per-rate table; `TXPWR_LMT.txt` is not applied because power-limit enforcement is off | `/firmware/rtlwifi/` | stock firmware |
| `soc_msc.sh` sets MSC1: non-removable, 4-bit, `sdio` speed, 100 MHz maximum, keep power in suspend, `msc1_sdio_clk=y`. `soc_msc.ko` registers through `sdhci_add_host`, which enables in-band SDIO interrupts | `soc_msc.sh`; `soc_msc.ko` | stock firmware |
| `/usr/bin/wifi_connect.sh` runs `wpa_supplicant -Dnl80211 -iwlan0 -c/mnt/data/wpa_supplicant.conf -B`. The player adds networks over the control socket and saves them with `SAVE_CONFIG`. Once associated it runs `udhcpc -i wlan0`, whose default script writes `/etc/resolv.conf` (a link to `/tmp/resolv.conf`) | scripts; `demo` | stock firmware |
| Time: `ntpdate` (servers `cn.pool.ntp.org`, `time.apple.com`, `time.windows.com`), then `hwclock -w`; `hwclock -s` at start. The RTC holds UTC. The time zone is `TIMEZONE=INDEX` in `/mnt/data/config.ini` | `demo` strings | stock firmware |
| Signal level: `/proc/net/rtl8733bs/wlan0/rx_signal` and `signal_strength` | Q2 Pod | unverified |
| Shairport 1.1.1 and the `-M /tmp/dirairplay` metadata path | Q2 Pod | unverified |

### Power save (LPS) rules

From `traffic_status_watchdog` and `traffic_check_for_leave_lps` in
`rtl8733bs.ko` (stock firmware). The build uses Realtek's legacy rules;
`rtw_lps_chk_by_tp=1` has no effect.

- The watchdog runs every 2 s. LPS may be entered only after a period with
  at most 2 unicast receive packets and at most 8 receive plus transmit
  packets.
- A busy period (more than `rtw_busy_thresh`, 40, packets) raises a
  transition count to at least 5, plus one per further busy period, at most
  30. Each quiet period lowers it by 2; entry needs 0.
- In LPS, a fifth unicast receive packet within a period, or a ninth
  transmit packet within 2 s, leaves LPS at once.

## Bluetooth

The firmware files, their hashes and update sources are in
[bluetooth-firmware.md](bluetooth-firmware.md).

| Fact | Source | Level |
| --- | --- | --- |
| `demo` runs `rtk_hciattach -n -s 115200 ttyS0 rtk_h5`: H5 on UART0 at 115200 baud, then the rate from the configuration file's UART entry (offset 0x000c, rate code `0x04928001`, 3,000,000 baud, hardware flow control). `hci_uart_h5_kernel_5_10_21.ko` is the line discipline | `demo`; `rtl8733bs_config` | stock firmware |
| The configuration has no address entry; the controller's address comes from its eFuse | `rtl8733bs_config` | stock firmware |
| Stack: `bluetoothd` (BlueZ) over `dbus-daemon`; `bluealsa -c LDAC -c aptX-HD -c aptX -c AAC`; `hciconfig hci0 class 6c020c`, `sspmode 1` | `demo` | stock firmware |

### Bluetooth UART rate in the stock kernel

The stock kernel's `serial_ingenic` driver reaches 3 Mbaud from the 24 MHz
crystal (stock firmware: kernel image disassembled with its symbol table).

- The UART0 node (`serial@0x10030000`, `ingenic,8250-uart`) has one clock,
  the 24,000,000 Hz `ext` clock. No PLL, divider or `assigned-clock-rates`
  is involved.
- `serial_ingenic_set_termios()` looks the rate up in a 30-entry table of
  {baud, divisor, UMR, UACR}:

  | Baud | Divisor | UMR | UACR |
  | --- | --- | --- | --- |
  | 4,000,000 | 1 | 6 | 0 |
  | 3,000,000 | 1 | 8 | 0 |
  | 2,000,000 | 1 | 12 | 0 |
  | 1,500,000 | 1 | 16 | 0 |
  | 921,600 | 2 | 13 | 0 |
  | 230,400 | 6 | 17 | 0x252 |

  A rate not in the table goes through `get_divisor()`: 16 × divisor if that
  divides the clock exactly, else a search over divisors with M from 4 to 32
  and a UACR pattern from each bit's timing error; the M nearest 16 wins.
- Write order: LCR = line format; later LCR = 0x80 (DLAB), DLL, DLM,
  LCR = the saved format (DLAB clear), then UMR (0x24) and UACR (0x28), then
  FCR = 0xd1 (receive trigger 60, UME, FIFO on). All writes are 32-bit.
- `serial_ingenic_startup()` writes FCR 0x01, 0x17, 0x00 (UME clear),
  LCR 0x03, ISR (0x20) 0 and IER 0x15 once, before the first termios. A later
  rate change, such as `rtk_hciattach`'s switch from 115200 to 3 Mbaud, runs
  with UME set.
- The X1000 programming manual's sequence (23.8.1) clears UFCR.UME, sets the
  divisor, format and FIFO, then sets UME. Its UMR and UACR descriptions do
  not mention DLAB; its register table lists UMR as read-write and its field
  table as write-only (datasheet).

Writing UMR and UACR after DLAB is cleared, as above, gives a working
3 Mbaud link; UMR reads back as written in either order (device-verified).
Whether UME must be clear when M changes is not known.

## Power, charging and USB

| Fact | Source | Level |
| --- | --- | --- |
| Power-key node `powerkey@0` (`x-powers,axp2101-pek`): `pmu_powkey_off_time` 10,000 ms, `pmu_powkey_off_en` 1, `pmu_powkey_off_func` 0 (power off), `pmu_powkey_long_time` 1,500 ms, `pmu_powkey_on_time` 510 ms. The AXP2101 branch of `axp20x_pek_probe` (OF match data 8) writes register 0x27 = 0x1d (ONLEVEL 1 = 512 ms, IRQLEVEL 1, OFFLEVEL 3) and sets 0x22 bits 1:0 | embedded device tree; kernel disassembly | device-verified (register value) |
| Charger node: 1,000 mA for running, suspended and powered-off charging; 100 mA termination; 2,000 mA USB input limit; `pmu_init_chgvol=0x10fe` (4,350 mV), which `axp2101_charger_probe` maps to selector 4 of register 0x64 | embedded device tree; kernel disassembly | stock firmware |
| Battery: `/sys/devices/i2c-0/0-0062/cw2015_capacity` and `/sys/class/power_supply/battery/status`. `time_to_empty_now` stays at 4800 and is not an estimate | Rockbox; sysfs | device-verified |
| Suspend: `mem` to `/sys/power/state`; a key resumes | Q2 Pod | unverified |
| Power-off (`into_poweroff`): stop the player, DAC power 0, GPIO PC20 and PE22 `output0` (`ioctl(/dev/gpio, 0x20004778, {pin, 1, "output0"})`), `screen_action(0)`, then `poweroff`. A plain `poweroff` without these steps can leave a white screen that needs a reset | `demo`; Rockbox | stock firmware |
| OTG: `check_otg_status` (`0x004e8ec0`) reads PD18. On a change to 0 it sets PC20 then PE22 to 1; on a change to nonzero it sets both to 0 in the same order | `demo` GOT targets and branches | stock firmware |
| USB detect: `usb_detect` (`0x004f4ac4`) reads PD17 and returns true when it is 0 | `demo` | stock firmware |
| The stock DWC2 node is `dr_mode="otg"` with no VBUS, ID or switch GPIOs. The kernel is built with host and USB audio code (`dwc2_hcd_init`, `snd_usb_create_stream`) | embedded device tree; kernel symbols | stock firmware |
| `demo` contains a `bq25890_1_otg_enable` path, but the Q2's device tree describes no BQ25890; it belongs to another model | `demo`; device tree | stock firmware |
| USB storage mode needs the stock player running (`pc_link` stays 0 otherwise) | Rockbox | unverified |

## Clicker MCU

The click is generated by MCU firmware, not by Linux (stock firmware).

- The player runs `cmd_mcu write_firmware /usr/data/libmcu-bare.bin`,
  `bootup` and `reset`, then `cmd_mcu write_str buzzer` per click. The
  vendor `soc_mcu` module provides `/dev/mcu`.
- The firmware loads at MCU address 0xf4000000. The command compare at
  0xf4000fdc leads to the click routine at 0xf4000e9c.
- The routine requests GPIO 65 (PC01); the table at 0xf4003efe maps it to
  PWM channel 1, function 0. It fills four high and low pairs with 0xd400
  (54,272), starts looping DMA waveform output (configuration at
  0xf4003cac), waits 2 ms, stops the DMA and returns PC01 to GPIO low.
- With the PWM clock at 300 MHz (the stock `div_pwm` rate in `clk_summary`),
  54,272 high plus 54,272 low counts is 2,763.86 Hz. The waveform was not
  measured.

## Other drivers

| Fact | Source | Level |
| --- | --- | --- |
| The stock kernel registers the CST8xxT touchscreen (`hyn_ts`) and requests AXP2101 ALDO3 at 2.8 V for it; PA14 controls reset | `hyn_touch.ko`; device tree | stock firmware |
| `soc_adc.ko` loads with `adc_vref=1800` and `cmd_adc` is shipped | modules; root | stock firmware |
| `ingenic,x2000-efuse` is enabled and `cmd_efuse` is shipped; the player reports `mac_addr` and `bt_mac_addr` in its device information | device tree; `demo` | stock firmware |
| The kernel includes `vfat`, and `mkdosfs` is shipped | kernel; root | stock firmware |
| No AES, hash or RSA driver binds; `/proc/crypto` lists only software algorithms | procfs | device-verified |
