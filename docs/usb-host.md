# USB connection

The Q2's USB-C port runs as a USB device. The image offers a management
network (RNDIS) and, on request or on attachment to a computer, the microSD
card as a mass-storage LUN. It does not run the port as a USB host, so an
external USB DAC is not supported. The controller's identity and the stock
firmware's OTG pins are in [hardware.md](hardware.md#usb); the card handoff
protocol is in [usb-storage.md](usb-storage.md).

## Controller role

| Fact | Source | Level |
| --- | --- | --- |
| The Q2 kernel builds DWC2 peripheral-only (`CONFIG_USB_DWC2_PERIPHERAL=y`) and the board device tree sets `dr_mode = "peripheral"` on the OTG controller | q2-nova/linux `shanling_q2_production_defconfig`, `shanling-q2.dts` | vendor source |
| The gadget runs descriptor DMA (`g_dma=1`, `g_dma_desc=1`) | the driver's cached `params` in debugfs | device-verified |
| The X2000 USB PHY driver has power-on and power-off operations only; no VBUS, ID or switch GPIO is controlled | q2-nova/linux PHY driver | vendor source |
| The stock device tree has `dr_mode = "otg"` but none of the PHY's optional VBUS, ID, switch or wake GPIOs. The stock player drives PC20 and PE22 on PD18 transitions | stock kernel image and `demo` (`os/audio/stock_usb.py`, `os/audio/stock_board_usb.py`) | stock firmware |
| The stock `demo` contains a `bq25890_1_otg_enable` path, but the Q2's device tree describes an AXP2101 and no BQ25890 | stock `demo` strings; stock device tree | stock firmware |
| Shanling's product page lists external DACs and USB-C headphones as supported | manufacturer product description | unverified |
| The QEMU X2000 model runs the controller in device mode. `usb-host-fixture=on` replaces it with QEMU's DWC2 host at the Q2's address and interrupt, for component tests; it does not model the board's role or VBUS | qemu fork `hw/mips/x2000/usb.c`, `usb-host.c`; [board doc](https://github.com/q2-nova/qemu/blob/q2/10.1/docs/system/mips/shanling-q2.rst) | emulator |

A host role needs the Q2's connector CC/ID wiring, the VBUS source, current
protection and isolation from a powered host. None of these is known, and no
role, VBUS, GPIO or PMIC control has been driven to find out. The stock
inspectors reproduce the code evidence from public firmware, without a
device:

```sh
python3 -m unittest discover -s os/audio -p 'test_stock*.py'
python3 os/audio/stock_usb.py PRISTINE_RECOVERY_XIMAGE
python3 os/audio/stock_board_usb.py PRISTINE_DEMO
```

They accept only the pinned pristine V1.32 inputs.

## Gadget functions

- **Management link.** `os/hardware/usb` (diagnostic and RAM images) creates
  gadget `q2diag` with one RNDIS function, a fixed point-to-point `/30`
  address and no DHCP, DNS or default route, and disables IPv6 on `usb0`.
  The emulator image's `S40q2-usb` creates `q2net` instead.
- **Card LUN.** `q2-usb-storage start` adds `mass_storage.q2card` to the
  existing gadget beside RNDIS ([usb-storage.md](usb-storage.md)).

## Cable detection

Two signals are used, for different questions:

- **Physical VBUS:** `/sys/class/power_supply/axp20x-usb/online` reads the
  AXP2101's VBUS-good bit. Unplug is confirmed only by three readings of
  exactly 0, 100 ms apart.
- **Host configured:** the UDC's `state` is `configured`. UDC state also
  changes on the Q2's own unbind and rebind, bus resets and enumeration, so it
  never counts as unplug.

## Automatic drive mode

`q2-usb-auto` ([os/platform/usb-auto](../os/platform/usb-auto)) exports the
card when the host has configured the gadget and VBUS is present; a charger
alone does not stop playback. Its command is `q2-usb-auto
start|run|stop|inhibit|resume`.

- One export attempt per physical attachment, including a failed one, so a
  safe eject does not re-export at once. Only confirmed VBUS loss clears the
  attachment latch.
- `/run/q2/usb-auto.inhibit` (`inhibit`, `resume`) and the boot argument
  `q2.usb-auto=off` stop new exports; return watching continues for existing
  sessions. `stop` stops the manager and its return watching.
- A failed return keeps the session and the manual recovery path. Exit 75
  from the helper (an unconfirmed sample or a held lock) releases the attempt
  so a later real return can proceed; other failures do not loop.
- With no session and no latch, the manager does not take the ownership lock
  to recheck an idle battery, which keeps it out of the way of system sleep.

`q2-usb-auto-events` ([os/platform/usb-auto-events.c](../os/platform/usb-auto-events.c))
waits on the UDC `state` attribute (POLLPRI) and kernel uevents for UDC and
power-supply changes, with a 30 s safety recheck. While a session is armed or
exported it rechecks every 1 s, because a host's SCSI eject closes the
backing file without a configfs notification. Deadlines are absolute, so
unrelated events cannot postpone a check. The shell manager checks the
child's interpreter, script and arguments before signalling it, and reaps it
on stop. Installed root init starts the manager after the player service is
admitted; safe power-off stops it before the player and network.

### Drive-mode screen

`q2-usb-status` ([os/platform/usb-status.c](../os/platform/usb-status.c)) draws
the screen through the KMS bridge while the card is away. It does not start
the player, load configuration, open audio or read the card. It reads the
session phase every 250 ms and redraws only when the text changes.

| Phase | Screen |
| --- | --- |
| preparing, detached, armed | Connecting to computer |
| exported | USB Drive Mode: Edit files on your computer; Eject or unplug to return |
| returning, or no phase without a setup hold | Returning to player |
| local-ready, or no session with `/run/q2nd/start-held` | Finishing setup |
| error | USB needs attention, with a neutral action and no paths or raw errors |

`q2-usb-status-control` ([os/platform/usb-status-control](../os/platform/usb-status-control))
starts, adopts and stops the screen. It checks the recorded PID's executable
and the first-frame ready handshake (`/run/q2/usb-status.{pid,ready}`); a
foreign or invalid PID is refused. The screen holds no descriptor on the card.
The player's normal launch stops it and waits for display teardown before
taking DRM.

## Qualification

| Check | Result | Level |
| --- | --- | --- |
| `python3 os/test_usb_storage.py`, `os/test_usb_auto.py`, `os/test_usb_auto_idle.py`, `os/test_usb_disconnect_startup.py`, `os/test_usb_status.py`, `os/test_usb_runtime.py` | helper protocol, manager policy (three-sample unplug traces, rapid reconnect, connected eject, disabled automation, foreign backing, teardown and remount faults), startup ordering and screen states with generated fixtures | host |
| Composite RNDIS and card LUN enumerate; a generated file written by the host is fsynced and reads back on the Q2 with the same hash | Q2 and a Linux host | device-verified |
| The Q2 refuses a local remount before host eject. `udisksctl power-off` removes the USB device but does not close the LUN; UDisks2 `Drive.Eject` does | Q2 and a Linux host | device-verified |
| Installed image: automatic export on attachment, host eject and local return with the player healthy | installed image | device-verified |
| USB cable removed and reinserted with the card local: same boot, card and register checks pass | installed image | device-verified |

Not shown on a Q2: Settings menu export followed by automatic return (a menu
export has run; its automatic return failed); physical unplug and reconnect
with the LUN exported; repeated, busy and failing cycles; interrupted
management; hosts other than Linux.
