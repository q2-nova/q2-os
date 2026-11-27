# Bluetooth attachment

The Q2's Bluetooth controller is the Bluetooth side of the RTL8733BS combo
chip. The host stack (H5, HCI, L2CAP, AVDTP, AVRCP and the codecs) runs inside
the player, `q2nd`, on the UART; the kernel's Bluetooth subsystem is not
built. The stack, the audio route, the settings and the probe commands are in
q2-player [docs/bluetooth.md](https://github.com/q2-nova/q2-player/blob/main/docs/bluetooth.md).
This page covers what the OS provides: the wiring, the kernel support and the
firmware files.

## Hardware path

| Fact | Source | Level |
| --- | --- | --- |
| Controller: RTL8733BS Bluetooth (Realtek 8723F family, patch project ID 19). HCI revision 0x000f, LMP subversion 0x8723, manufacturer 0x005d from ROM; ROM version 2 | stock `rtl8733bs_fw` trailer; HCI Read Local Version | device-verified |
| HCI three-wire UART (H5), 8 data bits, even parity, 115200 baud until the patch is loaded | stock `rtk_hciattach -n -s 115200 ttyS0 rtk_h5`; H5 bring-up | device-verified |
| UART0 (`ttyS0`, 0x10030000): PD23 RXD, PD24 TXD (`uart0-data`), PD25 CTS, PD26 RTS (`uart0-hwflow`); all four pins are claimed by `10030000.serial` | pinmux in debugfs | device-verified |
| RTS/CTS flow control works on the board at 1.5 and 3 Mbaud | HCI probes with flow control | device-verified |
| BT_DIS_N is PD20, active high. It also enables the Wi-Fi slot's supply chain, so it drops (and resets the controller) only when neither Bluetooth nor the Wi-Fi slot holds it. There is no separate reset line | stock `rtk_bt_power_ctrl`; controller resets with Wi-Fi off | device-verified |
| UART0's clock is the 24 MHz crystal. The 16 × divisor gives 1.5 Mbaud exactly; 3 Mbaud needs UMR 8 with divisor 1 and UACR 0, as in the stock driver's table | stock device tree and `serial_ingenic_set_termios`; X1000 programming manual | stock firmware |
| Measured line rates (300,000 bytes, transmitter drained): 1.42 Mbaud at a 1.5 M setting, 2.70 Mbaud at 3 M, 1.86 Mbaud at 2 M, 3.45 Mbaud at 4 M. The shortfall is interrupt gaps between FIFO refills | timed transfer with the controller in reset | device-verified |

What the stock firmware runs (BlueZ, bluez-alsa, `rtk_hciattach` and the
configuration's rate entry) is in
[stock-firmware.md](stock-firmware.md#bluetooth), with the stock kernel's
UART rate sequence.

## Kernel

The Q2 kernel is [q2-nova/linux](https://github.com/q2-nova/linux), branch
`q2/7.3`. Its production configuration has `CONFIG_BT` off. Bluetooth needs:

| Part | What it does |
| --- | --- |
| UART0 pin groups in `shanling-q2.dts` | mux `uart0-data` and `uart0-hwflow` for the controller |
| `bt-en` fixed regulator on PD20 and a `regulator-output` consumer, `bt-power`, with `CONFIG_REGULATOR_USERSPACE_CONSUMER` | lets `q2nd` hold BT_DIS_N while the Wi-Fi slot is powered down: it writes `enabled` to `/sys/devices/platform/bt-power/state` when Bluetooth starts and `disabled` when it stops. Fixed regulators share the GPIO by reference count, so the line is high while either regulator is on |
| UMR and UACR support in `drivers/tty/serial/8250/8250_ingenic.c` | a rate the 16 × divisor reaches within 2 % is programmed as before; any other uses UMR (6 to 16 clocks per bit), giving 2, 3 and 4 Mbaud exactly. UMR and UACR are written after LCR clears DLAB, as the stock kernel does. `8250_ingenic.umr_seq` (0644) selects the write order for tests: 0 stock (default), 1 stock with FCR.UME clear while M changes, 2 with DLAB still set. A rate with M other than 16, or a register that does not read back, is logged |

Without the `bt-power` hold, `q2nd` refuses to start Bluetooth while the
Wi-Fi slot is unbound, and powering Wi-Fi off during listening resets the
controller.

## Firmware files

The controller needs Realtek's patch and configuration, `rtl8733bs_fw` and
`rtl8733bs_config`, from Shanling's V1.32 update. They are not
redistributable from this repository. `tools/fetch_firmware.py` fetches them;
`os/install/build.py --bluetooth-firmware DIR` installs them under
`/lib/firmware/rtlbt` after `os/bluetooth_firmware.py` checks their SHA-256
digests, and refuses a kernel without `CONFIG_REGULATOR_USERSPACE_CONSUMER`.
`q2nd` also looks in `/state/bluetooth/firmware`. Hashes, versions and update
sources are in [bluetooth-firmware.md](bluetooth-firmware.md).

Tests: `python3 os/test_bluetooth_firmware.py` admits only the pinned files.
