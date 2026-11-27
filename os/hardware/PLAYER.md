# Physical application RAM image

`build-player.py` builds a RAM-only image with the full Buildroot userspace
and the mainline player, for bringing up the Q2's peripherals one driver at a
time. It is loaded into RAM over the installed system and is not an
installable release: it has no NAND access, and a reset returns the Q2 to the
installed system. Its `artifact.json` and `/etc/q2-release.json` record
`hardware_qualified: false`.

The board facts this image depends on are in
[docs/hardware.md](../../docs/hardware.md); the loader and watchdog are in
[docs/boot-chain.md](../../docs/boot-chain.md).

## Build

Inputs are a generated Buildroot target, a generated client public key and a
generated Dropbear host key. The image never takes a key, root filesystem or
configuration from a used device.

```sh
python3 os/install/prepare.py --dest build/os/player-hardware-src
python3 os/hardware/build-wifi-tools.py --buildroot-output BUILDROOT_OUTPUT
python3 os/hardware/build-player.py \
  --kernel-source build/os/player-hardware-src \
  --buildroot-target BUILDROOT_OUTPUT/target \
  --authorized-key GENERATED_CLIENT_KEY.pub --host-key GENERATED_HOST_KEY \
  --wifi-tools build/os/hardware-wifi-tools
```

The kernel source is a dedicated checkout of the production Linux revision
in `os/sources.json`; the builder refuses any other revision. It edits that
tree's device tree in place (command line, 32 kHz sleep clock for the
RTL8733BS, SFC disabled) and applies the patches in `os/hardware/patches/`
and the watchdog patch from `os/patches/kernel/` where the revision does not
already carry them.

- `--player-binary` and `--platform-library` copy a generated `q2nd` and
  `libq2platform.so` into the image without changing the Buildroot target.
- `--boot-cpus 1` adds `maxcpus=1`; the default boots both CPUs.
- `--wifi-tools` adds `wpa_supplicant` and `wpa_cli` from
  `build-wifi-tools.py` and builds the RTL8733BS module. The default profile
  is WPA2 personal with internal crypto; `--wpa3` builds the SAE profile
  ([wifi-security.md](../../docs/wifi-security.md)). This `wpa_supplicant` has
  no `-f` log option; redirect its output instead.

Outputs are under `build/os/player-hardware/`: the ELF `vmlinux`, the external
`shanling-q2.dtb` and `artifact.json` with the digests of every input, module
and patch. The ELF and DTB are RAM-loader inputs. They must not be packaged
as `xImage` or written to NAND; the initramfs is larger than the kernel slot.

The builder checks the resolved kernel configuration and stops when:

- `CONFIG_MTD` is set (the image must not reach NAND; the device tree also
  disables the SFC);
- loop, ext4, exFAT or the configfs mass-storage function is not built in;
- the kernel is not the two-CPU SMP build;
- I²C, the X2000 display driver or the Q2 sound card is built in rather than
  as a module.

The kernel command line is `console=ttyS3,115200 clk_ignore_unused panic=10
q2.hardware=1`, without `earlycon`.

## Boot

`player-init` runs as `/init`:

1. Mounts the kernel filesystems and starts the USB management link
   (`q2-diagnostic-usb`, the address in [README.md](README.md)) with Dropbear,
   key-only, on the provisioned host key.
2. Loads `i2c-gpio`, `axp20x-i2c`, `axp20x_usb_power` and `axp20x_battery`,
   so the 4.35 V charge limit from the device tree is applied before any
   other peripheral. A charged cell exceeds the PMIC's 4.2 V reset value and
   the Q2 restarts when USB is removed.
3. Waits up to 15 s for the microSD card, mounts partition 1 (exFAT) and
   loop-mounts a 128 MiB ext4 state image from a hidden directory on it at
   `/state` (path in `player-init`). Init never creates, formats or searches
   for a state image. A missing image leaves `/run/q2/state-status` at
   `missing` and SSH available. A new state gets
   `/etc/q2-defaults/hardware-fixture.toml`, which points at a generated
   Navidrome fixture on the host end of the USB link.
4. Starts the clock services, prints `Q2_PLAYER_HARDWARE_READY` and saves
   `dmesg` to `/run/log/boot`. It does not start the player.

UART0 gives a shell only when the command line has `q2.emulated=1`, for the
emulator. On the Q2, UART0 belongs to the Bluetooth controller.

## Peripheral bring-up

The peripheral drivers are modules so that each probe can be watched over SSH.
`q2-player-bringup` loads them in order and checks each binding, not only the
`modprobe` result:

```sh
q2-player-bringup --audio-only
q2-player-bringup
```

1. Shared power and buses: `i2c-gpio`, `axp20x-i2c`, `axp20x_usb_power`,
   `axp20x_battery`, `axp20x-regulator`, `i2c-jz4780`. The touchscreen driver
   is tried when the device tree has it; its failure is logged and does not
   stop bring-up.
2. Audio: the `shanling-q2` machine driver binds first and sets the board's
   mute, mode and high-volume lines to the stock idle levels, with the stock
   200 ms mute settling time. Then `x2000-audio` and `snd-soc-cs43130`, and a
   check that all four codecs (I²C5 0x30 to 0x33) and the sound card are
   bound. Success writes `/run/q2/audio-ready`.
3. Display, in parallel with audio: `pwm-ingenic-x2000`, `pwm_bl`,
   `panel-shanling-q2`, `ingenic-x2000-dsi`, `ingenic-x2000-dpu`, a check for
   `/dev/dri/card0`, then the start-up splash.
4. Then the wheel, the CW2015 gauge, the RTC and the clicker
   (`pwm-beeper`, or `snd-x2000-pwm-audio` when the device tree selects it),
   with a check for each device. Success writes `/run/q2/peripherals-ready`.

A codec module that is loaded without the audio-ready marker means an earlier
attempt ran out of order; the helper refuses and the image needs a fresh boot.
A completed run can be repeated. The helper starts no network, no player and
writes no PMIC register.

With the peripherals ready, inspect `/proc/asound/cards`, `/dev/dri` and the
input devices, then start the player over SSH:

```sh
q2nd --mainline --data /state/Q2Navidrome
```

Wi-Fi uses generated test-AP credentials only. The RTL8733BS enumerates
asynchronously after MSC1 binds; wait for `wlan0` before configuring it.

## Board requirements in the Q2 kernel

These are the hardware behaviours the kernel
([q2-nova/linux](https://github.com/q2-nova/linux), branch `q2/7.3`) and this
image meet. Each was shown on a Q2 with this image unless the level says
otherwise.

| Requirement | Where it is met | Level |
| --- | --- | --- |
| X2000 GPIO interrupts on both edges need `PAT1=1` with `EDG=1`. In level mode the button interrupts storm and boot does not finish; with it each key gives one press and one release (Next 163, Previous 165, Play/Pause 108, Back 103) | `drivers/pinctrl/pinctrl-ingenic.c` | device-verified |
| The DPU's first descriptor address is written after the LCD clock is enabled, before DMA starts. Written earlier, every frame times out | `drivers/gpu/drm/ingenic/ingenic-x2000-dpu.c` | device-verified |
| `LayCfgEn` gives all four layers distinct order values (stock `0xe410`), including disabled layers. With all orders zero the panel stays blank while the composer runs | `ingenic-x2000-dpu.c` | device-verified |
| The AHB0 gate (CLKGR0 bit 29) is critical. As a gate under the DSI clock it is turned off with DSI's last reference and the system stalls at screen blank | `drivers/clk/ingenic/x2000-cgu.c` | device-verified (blank, restart and disable cycles with 40 page flips) |
| I²S1's master clock is the SoC's MCLK on PC8 at 512 × fs for 44.1 and 48 kHz; the DACs have no crystal. Fractional divider rounding within 100 ppm is accepted (22,579,195 Hz for 22,579,200) | Q2 sound card and device tree | device-verified (PCM transport at 44.1 kHz S16_LE and 48 kHz S32_LE; clock rates are clock-framework values) |
| The codecs probe only after the machine driver has set the board lines; with that order all four probe from a cold start with DCDC5 on | Q2 sound card; `q2-player-bringup` | device-verified |
| ALDO1 stays on at 1.8 V for the Wi-Fi SDIO slot (`regulator-always-on`); ALDO3 supplies the touchscreen | device tree | device-verified (ALDO1) |
| The battery limit is 4.35 V through `simple-battery` and `monitored-battery`; a re-probe that starts at 4.2 V sets 4.35 V | device tree; `axp20x_battery` | device-verified |
| The wheel's application identity bytes read 00/00 on the Q2; the driver does not reject them. Reports span positions 1 to 200 with releases | `shanling-q2-wheel` driver | device-verified |
| `CONFIG_FB_DEVICE` is off; the player uses DRM/KMS and there is no `/dev/fb0` | `player.config` | device-verified |

## Verified with this image

- Both CPUs online at boot; CPU1 offline and online with cross-core transfer
  checks and no coherency mismatch (device-verified).
- Display, buttons, wheel, gauge, RTC and clicker register through
  `q2-player-bringup`; the player UI renders and wakes after idle
  (device-verified).
- The mainline player streams from the generated Navidrome fixture over
  WPA2/CCMP and plays audibly in both ears on battery, with the four codecs
  at S32_LE 44.1 kHz (device-verified).
- The DAC gains are set at start; volume changes during a tone give no pops
  (device-verified, by ear). Pause, start and stop transitions are not
  checked.
- 100 PCM open and close cycles return the I²S gate counts to zero
  (device-verified).
- The RTC accepts and reads back a time from the host; retention across a
  power cut is not checked.

## Open

- Entering the image from a running full image through the old loader powers
  the Q2 off: the PMIC's power-off handler runs before the loader's
  `pm_power_off` hook (source reading; not traced). The RAM loader path from
  the installed system works. Native kexec is a separate check.
- After USB removal, a PMIC power-off status of 0x20 (DC-DC undervoltage)
  remains latched until power-on reset, so it cannot be tied to one event.
- The electrical reason DCDC5 interferes with codec power-up in the wrong
  probe order is not measured.
