# Physical application OS qualification

The SD fix is installed as diagnostic V0.10; see
[the physical SD evidence](../../docs/bugs/sd-card-cold-boot.md).
The application profile here is **RAM-only**, not an installable release.
It includes the full generated Buildroot userspace and existing mainline player,
with the qualified cache, ERET, sleep-clock and GPIO A fixes. It leaves NAND
inaccessible. Reboot returns to installed V0.10.

## Build

Use a generated Buildroot target/SDK and generated qualification SSH keys,
never the owner's root filesystem, keys or configuration:

```sh
KSRC=build/os/player-hardware-src kernel/prepare.sh
python3 os/hardware/build-wifi-tools.py --buildroot-output BUILDROOT_OUTPUT
python3 os/hardware/build-player.py \
  --kernel-source build/os/player-hardware-src \
  --buildroot-target BUILDROOT_OUTPUT/target \
  --authorized-key GENERATED_CLIENT_KEY.pub --host-key GENERATED_HOST_KEY \
  --wifi-tools build/os/hardware-wifi-tools
```

Artifacts and input digests are under `build/os/player-hardware/`. The ELF
`vmlinux` and external `shanling-q2.dtb` are RAM-loader inputs. They must not be
packaged as `xImage` or written to NAND. This initramfs is larger than the kernel
slot. Packaging a small boot kernel and separate application root filesystem
remains subsequent work after peripheral qualification.

The initial state is a newly generated 128 MiB ext4 filesystem **inside a file**
at `/mnt/mmc/.q2-mainline-dev-20261102/state.ext4`. Its host source was
`build/sd-continuation/player-state.ext4`; the uploaded blank image matched
SHA-256 `3305d00dcd836c434f0486105a1285d09a5bf96b6bea7dab8654ebb2893210d2`.
This does not repartition or format the card. Init never creates/formats a state
image and never searches for an existing owner configuration. A missing image
leaves diagnostics available and state marked missing. Its default configuration
uses the generated fixture account at `192.168.77.1:4533`.

USB SSH keeps the diagnostic management address and MACs. The host key is
explicitly provisioned and password login is disabled. UART0 provides a fixture
shell only with the explicit emulator command-line flag `q2.emulated=1`.

## Peripheral qualification

`/init` mounts the generated state, starts SSH and records boot logs. It does not
automatically start the player. The I2C, PMIC, display, sound and RTC drivers are
modules so their probe can be observed over SSH. Use the ordered helper:

```sh
q2-player-bringup --audio-only
q2-player-bringup
```

The machine driver must bind before the codecs so mute/mode/boost GPIOs are
owned in their idle states. Patch 0044 asserts shared reset before enabling
codec power and waits the stock 200 ms mute settling interval. The helper
checks actual bindings, not merely successful `modprobe`; a completed run is
idempotent. A codec-first attempt without its ready marker requires a fresh boot.

Inspect `/proc/asound/cards`, `/dev/dri`, input devices and dmesg before starting
`q2nd --mainline --data /state/Q2Navidrome`. Physical display, button and audio
checks are separate from driver registration. Establish a generated Navidrome
fixture on the host USB link first; Wi-Fi association/streaming is a subsequent
test using generated AP credentials. The included `wpa_supplicant` uses nl80211
and internal crypto with the verified Buildroot-pinned sources; it is a WPA2
personal qualification build, not a claim of WPA3 support or security review.

## Current evidence — 2026-11-02

The first image (built-in peripherals and `earlycon`) reached full userspace,
DRM and ALSA in QEMU but failed to return USB on the physical RAM boot. With no
exposed UART, its failure point is unknown. Logs are under
`build/sd-continuation/player-hw1-ramboot.txt` and
`build/worktrees/boot-chain/build/ram-handoff/physical/host-kernel-player-hw1.log`.
The original image and DTB are retained in that worktree's
`build/ram-handoff/player-hw1/`.

An early-console stall is one hypothesis: the 8250 early console polls TX-empty
without a timeout, before normal UART clock initialization. The next comparison
DTB removes only `earlycon`; it does not establish that as the cause. A second
profile additionally defers unqualified drivers until SSH. Physical testing of
these candidates requires resetting the stalled RAM boot back to V0.10.
No application image was flashed. No saved Wi-Fi credentials were read.


The revised modular profile (including Wi-Fi tools) built successfully and
reached `Q2_PLAYER_HARDWARE_READY` in QEMU. That smoke run intentionally had no
SD image and reported missing state. It did not exercise deferred peripherals,
SSH authentication, player rendering or Wi-Fi association. Its logs are
`build/sd-continuation/player-emulator3-console.txt` and `player-emulator3.log`;
current artifact digests are in `build/os/player-hardware/artifact.json`.
The attempted UART module probe did not run: this emulator's direct uImage path
uses the embedded DT, ignoring the supplied external DT/fixture-shell flag.
The physical RAM loader uses UHI and does pass its external DT.

Host verification: Python compilation, shell syntax, resolved kernel config
(no MTD; SD/loop/ext4 built in; peripherals modular), and the 23 existing updater
tests passed. These are host/emulator checks, not physical application success.


## Physical continuation: GPIO interrupt correction

After the device owner reset the stalled device, removing earlycon alone and deferring
I2C/display/audio drivers still failed to reach application SSH. Both returned
to NAND V0.10 with a watchdog armed (nominal 90 s; actual fallback intervals were
not calibrated timeout measurements). The same application kernel/userspace
booted with the diagnostic DT. Disabling only `/keys` in the full application
DT also booted, with SSH and the generated ext4 state ready.

Patch 0036 corrects X2000 dual-edge GPIO IRQ setup: `PAT1=1` is required in
addition to `EDG=1`. Mainline had selected level mode (`PAT1=0`), causing the
button-equipped board to fail to finish booting. The exact stock kernel's
`ingenic_gpio_irq_set_type` selects function `0x1a` for IRQ type 3: jump-table
entry `0x808da098` points at `0x80827154`. Its shadow helper tests function bit 1
and writes PAT1-set at `+0x34`. Evidence: `stock-gpio-irq-type.dis`,
`stock-gpio-irq-table.txt`, `stock-gpio-shadow.dis` under
`build/sd-continuation/`.

The corrected kernel (build #8) booted with the complete board DT, mounted
state, and registered Q2 buttons. The device owner pressed Next, Previous, Play/Pause and
Back; every button produced one EV_KEY press and release (codes 163, 165, 108,
103 respectively). Interrupt counts remained bounded and the machine remained
responsive. Physical logs: `player-gpio-fix.txt`, `player-buttons.txt`;
`player-gpio-sha256.txt` identifies the kernel. This verifies these four buttons,
not the wheel or long-press behavior.

The AXP2101 driver and regulators also loaded on hardware. Display registration
produced frame-completion timeouts; audio module loading disconnected USB and
required another user reset. These are unresolved physical bring-up failures,
not qualified display/audio. The next tests keep audio unloaded and capture
individual probe stages. Installed NAND remains V0.10 throughout.

## Physical Wi-Fi association and transfer — 2026-11-02

Build #8 successfully associated with a generated WPA2-PSK/CCMP test AP on
2.4 GHz channel 6. `wpa_cli` reported `COMPLETED`; routing to the host test
address used `wlan0`. Four pings succeeded, and a 4 MiB generated HTTP fixture
downloaded with SHA-256
`2b07811057df887086f06a67edc6ebf911de8b6741156e7a2eb1416a4b8b1b2e`,
matching the host. Evidence under `build/sd-continuation/`:
`wifi-connected.txt`, `wifi-association2.txt`, `wifi-transfer.txt`.
This verifies association and data transfer, not Navidrome playback, Internet
access, DHCP, roaming or power saving. Only synthetic credentials were used.
The temporary host AP/profile and HTTP server were removed afterwards; host
Wi-Fi was returned to disabled with device autoconnect enabled, its original
settings. `wpa_supplicant` in this qualification build lacks the `-f` logging
option; redirect its foreground output instead.

Before that test, SDIO enumeration failed even after returning to NAND V0.10.
A read-only PMIC dump showed LDO enable register 0x90 was 0x08 rather than
the recorded working value 0x35; several voltage selectors also differed.
A bounded diagnostic module restored only LDO selectors 0x92–0x97 to
`0d 17 17 1c 0d 1c` and enable register 0x90 to 0x35, with readback checks.
It did not write the core DCDC rails. Rebinding MSC1 immediately enumerated
the Wi-Fi chip. Evidence: `pmic-after-player.txt`, `wifi-restore.txt` and the
ignored `pmic-restore/restore.c` probe.

The origin of those earlier PMIC changes remains unresolved. On the next
build #8 boot, separately loading I2C, the PMIC MFD, regulators and display
preserved the voltage selectors; display enable changed 0x90 to the expected
0x3f. Do not attribute the earlier corruption to the current regulator driver
without reproducing it. The unmodeled stock-enabled ALDO1/ALDO3 supplies also
need explicit treatment before enabling automatic regulator cleanup in a
production image. Evidence: `pmic-mfd-only.txt`, `pmic-regulators-only.txt`,
`display-restored.txt`.

Restoring rails did not fix display frame-completion timeouts. The current
driver writes its initial descriptor address before enabling the LCD clock;
a candidate defers that write until the clock is enabled. It is built only
under `build/`, not yet hardware-qualified or added to the patch series.
Unloading the existing display driver stalled the device and required another
physical reset; the candidate insertion returned busy and did not run.

### Display clock ordering verified; panel still blank

After the device owner's reset, a fresh build #8 RAM boot loaded the clock-order candidate
successfully. Patch 0037 retains an initial pending frame until LCD clock enable
and submits its address before starting DMA. The controller accepted address
`0x04d24080`, framebuffer registration completed in about 0.3 s without the
previous flip/vblank timeouts, and display-end interrupts advanced. Its TFT
status reported working. Evidence: `dpu-clock-fresh.txt`, `dpu-clock-state.txt`,
`dsi-live.txt` under `build/sd-continuation/`.

With brightness set to 4/9 and generated color bars written to fb0, the device owner
reported a lit but blank screen. Patch 0037 fixes controller startup only;
panel output remains unresolved. Static patch application and compilation
passed; this is not full visual qualification.

The next physical power cycle again presented the unusual PMIC voltage
selectors before the regulator module corrected them. This narrows their origin
to inherited cold-boot state rather than proving corruption by that module.
The exact boot stage responsible remains unverified.

### Stock audio startup audit

At the device owner's request, the exact stock `codec_cs43131.ko` was disassembled.
`cs43131_probe` initializes PB1 low, PB14 high, PB10 high, PB9 low and PB2 low.
`dac_pwr(1)` (`.text.unlikely+0x45c`) asserts mute, waits 200 ms, enables PB1,
then waits 50 ms. `cs43131_dac_init.part.0` (`.text+0x14c0`) pulses reset raw
0/1/0 with 20 ms intervals before register initialization. In contrast, the
current mainline codec enables its supplies before acquiring shared reset;
board mute/mode controls belong to a separately probed machine driver.
This establishes a sequencing difference, not its causal link to USB loss.

Stock I2C5 uses PD4/PD5 at 400 kHz. Its IRQ table gives Linux IRQ 64
(INTC line 56 with the stock base of 8), matching the mainline DT. The stock
codec also obtains `axp2101-aldo1` at 1.8 V, but its enable/disable calls are
in a separate ioctl branch through `regulator_bten`; this alone does not prove
ALDO1 is a DAC supply. Preserve that distinction when modeling power rails.
Evidence: `stock-codec.dis`, `stock-codec-symbols.txt`, `stock-i2c.dis` and
`stock-i2c-data.bin` under `build/sd-continuation/`.

The mainline audio-controller module loads successfully in isolation. The
codec module also loads with I2C5 unbound, so the failure requires probing the
hardware. A staged diagnostic now establishes the stock idle GPIO levels
before codec power-up and pauses between probe stages to preserve host logs.

That staged run confirmed the first codec reached the supply-enable call and
USB disconnected before it returned. Stock idle GPIO levels (PB10=1, PB9=0,
PB2=0, PB14=1) were requested and read back successfully beforehand. Thus those
levels alone do not fix the failure, and the subsequent shared-reset operation
and DAC register reads have not executed at the failure point. The precise
mechanism inside/after `regulator_bulk_enable` remains unproven; do not label it
a brownout without electrical evidence. Logs: `codec-staged-kmsg.txt`,
`codec-staged-probe.txt`, `codec-without-bus.txt`. The diagnostic module source
and staged codec source are retained under ignored `build/sd-continuation/`;
temporary logging/sleeps are not part of the kernel patch series.

Additional clue for the next reset: `pmic-mfd-only.txt` recorded register 0x21
as 0x20. The cached XPowers AXP2101 reference implementation identifies 0x21
as power-off status and bit 5 as DC-DC undervoltage. This predates the staged
run, so it cannot yet be attributed to that run; capture/compare the shutdown
reason before and after a controlled power-enable test. It supports investigating
the PMIC shutdown path, not a claim that a particular rail or brownout mechanism
has been measured.

The full RAM application artifact was rebuilt after patch 0037 with temporary
codec instrumentation removed. `build/os/player-hardware/artifact.json` records
kernel SHA-256 `6ba1273c0b98c60149d40541a4817d5d1e30f954c5f7197687bf4ec86e885a71`.
This rebuilt image has not been hardware-booted; installed NAND remains V0.10.

### DAC supply failure isolated to inherited DCDC5 state

The rebuilt image above has now booted on hardware (#10). A controlled diagnostic
on NAND V0.10 cleared only AXP2101 register 0x80 bit 4 (DCDC5 enable), changing
0x1f to the recorded working stock value 0x0f. After RAM handoff and the same
stock-idle GPIO setup, all four codecs identified as CS43131 revision A1 and
USB remained connected. The machine driver registered ALSA card 0. No DCDC
voltage selectors or other DCDC enable bits were changed. Logs:
`dcdc5-off.txt`, `codec-dcdc5-off.txt`, `audio-card-dcdc5-off.txt` under
`build/sd-continuation/`. This establishes a working power-state prerequisite;
the electrical reason DCDC5 conflicts with DAC power remains unmeasured.

A three-second generated silence WAV did not play: hardware parameter setup
failed waiting for XTAL_READY on codec 0. USB stayed responsive. Thus codec
identification and card registration are verified, but PCM playback is not.
Evidence: `audio-silence.txt`. Installed NAND remains V0.10.

### External DAC clock and PCM transport — 2026-11-02

Stock `x2000_510_cs4308p_sound_card.ko` selects a SoC MCLK at 512 times
the sample rate for 44.1/48 kHz (`ecodec_board_i2s_hw_params`, +0x4c).
In `soc_aic.ko`, `aic_set_sysclk` +0xb54 selects bank 2, pin 8; its GPIO
helper applies function 0x12. Mainline omitted PC8 from its I2S pin group
and incorrectly declared a crystal on each DAC. All four DACs reported
crystal errors when their crystal circuits were enabled together.

Patch 0038 removes the crystal declarations, selects the external clock family
per stream and includes `i2s1-sysclk-tx` (PC8). It also accepts fractional
divider rounding within 100 ppm instead of requiring exact integer equality.
The clock framework reports 22,579,195 Hz versus 22,579,200 requested, and
24,576,000 Hz for 48 kHz. These are software-derived rates, not instrument
measurements of the output clock. Static patch application and module builds
passed. The final source omits temporary clock logging.

Physical generated silence playback completed with return code 0 at
44.1 kHz/S16_LE and 48 kHz/S32_LE, with no new codec errors after PC8 was
routed. All four codecs subsequently reported suspended; another 44.1 kHz
playback resumed and completed successfully. This qualifies PCM transport
and that resume path, not audible output or analog fidelity. DCDC5 remained
off from the preceding diagnostic; cold-start initialization of that state
is still required. ALDO1 alone and ALDO3 alone did not resolve the earlier
crystal wait; both experimental changes were restored.

Evidence under `build/sd-continuation/`: `stock-sound-card.dis`,
`stock-aic.dis`, `audio-clock-summary.txt`, `audio-two-rates.txt`,
`audio-resume.txt`. Earlier clock-module trials mistakenly uploaded the
unchanged output-directory modules while the external-module build wrote to
the source directory. `audio-clock-corrected.txt` starts the corrected-module
results; `audio-mclk-pin.txt` records the subsequent PC8 correction.

### Stock display re-audit after combined-peripheral stall

Loading the display module sequence after the audio tests left USB enumerated
but SSH unresponsive. The command had no per-module markers, so its exact
failing stage is not established. The device owner supplied a reset and reiterated
reviewing the stock kernel, firmware and app before more variations.

Static findings from the exact extracted firmware:

- `lcd_h0209s.ko` has the same 50 DSI packets as mainline, digest
  `e726ec822393edd6c1d5df5a84be76f9c1e2fad657228f3990e854fe584e1583`.
  `h0209s_power_on` enables the first rail, waits 15 ms, enables the second,
  then pulses PA13 raw high/low/high with 10 ms intervals. Mainline's
  active-low reset descriptor expresses that same sequence.
- Stock `soc_fb.ko` calls `dsi_set_transfer_mode(0)` at `.text+0x5520`,
  before calling the panel power/init callbacks at +0x4e84/+0x4ea0.
  The helper clears the DSI command low-power selection bits. Mainline
  instead explicitly sets `MIPI_DSI_MSG_USE_LPM` for vendor packets and
  `MIPI_DSI_MODE_LPM` for standard DCS commands. A candidate removes both
  low-power requests to match stock; it is not hardware-qualified yet.
- The stock app's `lcd_linux_fb_create` at 0x62700c opens the framebuffer,
  requests fixed/variable info (0x4602/0x4600 at 0x62718c/0x6271a8), and
  maps its buffer at 0x6274f4. Nearby update code uses pan-display 0x4606
  and the wait-for-vsync ioctl. No panel-specific unlock was found in this
  inspected framebuffer path; this is not a claim about every app path.
- Stock layer descriptors additionally set bit 14 (`domain_multi`) and
  sharpen field value 1 (bit 20). Their relevance to the blank screen is
  not established; the high-speed command candidate leaves them unchanged.

Evidence: `stock-panel.dis`, `stock-panel.json`, `stock-fb.dis`,
`stock-mixer.dis`, `stock-app-symbols.txt`, `stock-app-fb.dis` under
`build/sd-continuation/`. The candidate module and `panel-original.c` are
retained there/in the prepared source tree. A proposed RAM trial could not
start after the reset because USB had not returned (`display-hs-boot.txt`);
it did not load a new kernel. Installed V0.10 remains unchanged.

### Display isolation after power-on

After the device owner turned the device on, the same RAM trial booted #10 successfully.
PMIC, I2C, PWM, backlight, HS-command panel, DSI and DPU modules loaded in
separate stages; USB remained responsive. The high-speed command candidate
alone did not produce a visible picture.

Correction to the earlier color-bar checks: this configuration has
`CONFIG_FB_DEVICE` disabled. The DRM framebuffer-console registration message
does not imply a userspace `/dev/fb0`; shell redirection created a regular file.
Those writes did **not** test displayed pixels. The file was removed. The player
uses DRM/KMS directly, so this does not require enabling the legacy interface
for the application. A direct `drmcheck` run then completed 20 page flips,
including its six-second idle interval. DRM state identifies its actual buffer,
and an uncached RAM read confirmed the generated gradient pixels. The device owner still
reported a blank screen.

Enabling the DSI host's built-in video pattern generator **did** produce a
visible pattern, confirmed by the device owner. This establishes a working panel/DSI
output path in this HS-command trial and narrows the remaining failure to the
upstream composer/pixel path. Disabling that generator restored the blank
screen. Applying the stock layer `domain_multi` bit and then opaque global
alpha (with descriptor re-queue) did not restore the gradient. These temporary
descriptor variations are not committed driver changes.

Stock emulation (not a physical measurement) captured a layer descriptor with
configuration `0x001560ff`, scale coefficients `0x200/0x200`, and stride 320.
Mainline used `0x000400ff`, zero scale coefficients and the same stride.
Raw logs: `display-hs-{boot,stages,drmcheck,drm-state,registers,desc}.txt`,
`display-hs-vpg.txt`, `display-domain-test.txt`, `display-alpha-test.txt`,
`stock-frame2-emulated.txt`, `stock-layer-emulated.txt`, under
`build/sd-continuation/`. The full application image with patch 0038 also built
successfully (kernel SHA-256
`75128b509b6fdb4301cd3fbed558a1c2b2e811b618458677f0d89e3f852a1966`),
but this hardware trial still runs #10 with individually supplied modules.

### Visible output: distinct hardware layer order

The stock descriptor comparison led to the actual blank-screen correction:
`LayCfgEn` must assign distinct order values to all four layers, including the
disabled ones. Mainline used `0x10` (only layer 0 enabled, all orders zero);
stock uses `0xe410` (layer 0 enabled, orders 0/1/2/3). The device owner confirmed the
gradient appeared when that field was changed. Restoring original scaler
coefficients, burst length and DSI timings while retaining only distinct layer
ordering preserved the visible gradient. Patch 0039 adds that field setting.
Patch application and the full RAM-image build passed; a fresh hardware boot
with the original LP panel-command mode is the next independent check.

Diagnostic correction: a composer writeback initially returned all black with
the experimental stock **ARGB** layer format and the fixture's zero alpha.
A comparison across RGB/ARGB and zero/opaque input alpha showed that mainline's
original RGB format correctly composed the gradient. That first black writeback
was a consequence of the diagnostic format change, not proof of a mainline
pixel-processing defect. The original RGB format is retained. Stock DSI timing
and the additional layer/scaler/burst settings are not part of patch 0039.

The device owner supplied a reference photo and noted that the visible pattern looked odd.
The test deliberately contains XOR checker-like regions and wraps its channels
at 256; native panel orientation also rotates it. The photograph establishes
visible content, but text orientation/color accuracy still need a simpler
pattern and player UI check. Photo conversion/reference renders remain under
ignored `build/sd-continuation/`.

Evidence: `display-writeback.txt`, `display-wb-matrix.txt`,
`display-latched-desc.txt`, `display-unity-test.txt`, `display-order-test.txt`,
`display-order-only.txt`, `display-order-original-timing.txt`. The first frame
read immediately after re-queue can still be the previous descriptor; later
latched reads and visible observations establish uptake.

### Fresh image and screen-off stall

The rebuilt image (#14, patch 0039 plus the original LP panel commands) booted
and launched `q2nd --drm-test` against the generated state directory. The device owner
confirmed the player UI looked correct. This mode uses real DRM/backlight but
simulated audio and scripted input; it is not full mainline playback validation.
The UI subsequently blanked on its 30-second inactivity timer and USB stopped
responding. The device owner confirmed that timing/observation and rebooted the device.

A controlled DRM blank test, with host-streamed stage logs, completed panel
display-off, sleep, rail shutdown, DSI shutdown and DSI clock gating. It stalled
at the subsequent DPU stop stage. A candidate moved composer stop to encoder
disable (before DSI post-disable), but that alone still stalled. This corrects
the initial inference that callback ordering alone caused the failure. Stock's
`disable_fb.part.0` calls `dpu_quick_stop_display` (+0x1468); its implementation
in `soc_fb_layer_mixer.ko` writes 1 to DPU control, whereas mainline writes 8
for general stop. A quick-stop candidate is the next test. The watchdog
recovered both controlled stalls without another physical reset.

Evidence: `display-layer-boot.txt`, `player-first-ui.txt`,
`display-stop-kmsg.txt`, `display-stop-fixed-{probe,kmsg}.txt` under
`build/sd-continuation/`. Patch 0040 is an uncommitted investigation candidate,
not a qualified fix yet. NAND remains V0.10.

### Screen-off cause: shared AHB0 gate

Further trials correct the shutdown-order hypothesis above. Moving the stop,
using stock's quick-stop command, synchronizing the IRQ, and moving DMA start
did not consistently preserve communication across blank/wake. Temporary
instrumentation reached the end of clock shutdown in one such trial, so the
earlier final log marker did not establish the CPU's exact stall instruction.

The mainline CGU model merges the AHB0 rate clock and global CLKGR0 bit 29
gate. DSI is its child. Releasing DSI's last reference can consequently gate
the shared system fabric, despite `clk_ignore_unused`. Stock's
`clk-x2000.c` instead separates `CLK_RATE_H0CLK` from `CLK_GATE_AHB0`, marks
the latter ignore-unused, and parents MIPI to the ungated rate clock.

Holding an AHB0 clock-framework reference, with the **original** composer and
panel drivers from #14, passed the complete physical DRM test: 20 flips,
blank, restart and final disable. Three additional complete runs passed and
USB remained responsive. This isolates the shared clock gate from the
discarded display lifecycle changes. Final patch 0040 marks AHB0 critical;
no frequency changes or experimental composer lifecycle changes are retained.
The previous uncommitted 0040 draft was replaced, with its evidence retained
under ignored `build/`. A full image with the final patch is building.

Evidence: `display-fabric-{boot,test,repeat,kmsg}.txt`, temporary
`dpu-desc/fabric_hold.c`, and the stock clock source under the cached vendor
tree. The AHB0 reference reports 300 MHz through the clock framework; this is
not a physical frequency measurement.

### Final AHB0 image qualification

The full RAM image (#18) with final patch 0040 passed two complete DRM runs
without the temporary clock-hold module: 40 completed frame flips in total,
with idle, blank, restart and final disable in each run. USB remained available.
An initial invocation used the wrong DPU module name and failed to open DRM;
the corrected run loaded `ingenic-x2000-dpu` and passed.

The original audio preparation also succeeded again: actual-wire DCDC5 disable
on V0.10, RAM handoff, then stock idle GPIO levels before codec probe. Raw PMIC
readback on #18 was 0x80=0x0f, matching the cache. All four DACs identified and
44.1 kHz S16 / 48 kHz S32 silence transfers completed successfully using the
built-in patch 0038 pin configuration. This establishes PCM transport, not
audible output. The faster regmap-based DCDC5 experiment previously powered
off the device; the cause of that difference and permanent cold initialization
remain unresolved.

The wheel probe returned identity 00/00, so normal mainline player input is
not ready. The next investigation compares its stock supply/reset sequence.
Evidence: `display-final-image-corrected.txt`, `audio-legacy-seed-probe.txt`,
`player-final-audio-wheel.txt`, `player-fabric-final-kmsg.txt` under
`build/sd-continuation/`. NAND remains installed V0.10.

### Physical wheel and first normal mainline UI

The wheel was replying on I2C, but its application identity bytes were 00/00.
Holding PB20 reset produced an I2C error, while releasing reset restored ACKs.
Separate stock-style write/read transfers, stock normal-mode commands, and
restoring the stock-observed ALDO3 setting did not change those identity bytes.
Stock `hyn_wr_reg` uses separate transfers, and `cst8xxT_updata_tpinfo` reports
the application bytes without requiring b6/02. Those expected values came from
the emulator's interpretation of the embedded firmware info block.

Removing the fixed-value rejection registered evdev input. Physical events
spanned positions 1..200 and included touch releases; the device owner confirmed that
the wheel moved the player menu in both directions. Patch 0041 retains I2C
error checks, DT matching and position bounds. A fresh #18 boot with the final
wheel module registered successfully without the ALDO3 experiment.

`q2nd --mainline` reached UI/network ready with real evdev and ALSA, using only
the generated state and Navidrome fixture. The device owner subsequently reported that
the sleeping screen would not wake. The USB link was absent, but the device owner then
clarified USB and 3.5 mm headphones cannot be connected together in his setup.
Consequently the lost USB link does **not** establish a system hang. He rebooted;
the wake issue needs observation over Wi-Fi. Audible playback is still pending.

Evidence: `wheel-{transaction,supply,normal-mode,reset-bus}-test.txt`,
`wheel-physical-events.bin`, `player-mainline-live.txt`, `player-wifi-live-*`,
and `stock-wheel.dis` under `build/sd-continuation/`.

### Wi-Fi rail and unplugged-power investigation

On the next cold full-image boot, BLDO1/2 matched their expected enabled voltages
but MSC1 still timed out. Enabling **only ALDO1** at the stock-observed 1.8 V
setting, then rebinding MSC1, enumerated the chip immediately. WPA2/CCMP
association, wireless SSH and HTTP to the generated Navidrome fixture passed.
Patch 0042 adds the always-on ALDO1 constraint; the rebuilt full image passed
compilation but has not yet been RAM-booted. Automatic probe ordering remains
separate from this runtime rail check.

The normal player remained responsive over wireless SSH with its backlight at
zero and DRM CRTC inactive. The device owner then disconnected USB to connect headphones
(the two connectors cannot be used together in his setup) and reported no wake.
Wi-Fi traffic stopped at that transition; no button events were captured.
Reconnecting USB **without pressing Power** booted V0.10 with a new uptime,
showing that the RAM session was lost. This separates the incident from the
qualified display-only blank/restart cycles, but does not by itself identify
the failing electrical rail or USB-disconnect behavior.

The PMIC read after that boot was power-on status 0x04 (VBUS), power-off
status 0x20 (DC-DC undervoltage), battery ADC 0x10f5 and reported percentage 100.
The shutdown status is read-only and retained until POR; it may include the
earlier DAC failure, so it is not conclusive attribution of this unplug event.
Reg 0x12=0 was checked against X-Powers AXP2101 datasheet V1.4, section
6.13.2.8: bit 3 applies to battery-only **power-off** state, not a general
force-on battery switch during normal operation. No BATFET change was made.
Source PDF: https://raw.githubusercontent.com/eggfly/AXP2101/main/AXP2101_Datasheet_V1.4.pdf
(SHA-256 `9e51d16d84d905c32a001a123be8e6c58213ee21e42d5bdb1458b731dba10f6d`).
Its reg 0x21 definition is section 6.13.2.18. Cached PDF/text remain under
ignored `build/ref/`; initial Poppler font warnings did not affect these
English register tables. Stock charger/MFD/regulator disassembly is being
compared before further power changes.

Evidence: `wifi-aldo1.txt`, `player-wireless-{app,kmsg,sleep-state}.txt`,
`player-wifi-heartbeat.txt`, `buttons-wireless-events.bin`,
`usb-unplug-pmic-status.txt`, `stock-{charger,battery-init,pmic-probe,regulator-probe}.dis`.
The next isolation test is USB removal on V0.10 without headphones or the player.

### USB-only isolation on installed V0.10

The device owner removed USB for approximately ten seconds with headphones disconnected,
then reconnected without pressing Power. V0.10 boot ID changed from
`a2a5b43c-332e-4b4d-b8e9-5f1cb4cffaea` to
`f117185d-8202-427b-8499-afdbd00f3fe1`; uptime restarted at eleven seconds.
The host recorded disconnect/reconnect at 18:25:02/18:25:12 local time.
Thus the problem also occurs without the player, audio or display drivers.
That boot still had DCDC5 enabled by the cold PMIC state. A second isolated
trial applies only the previously qualified DCDC5-off setting before repeating
USB removal, keeping headphones disconnected. The second trial also restarted:
boot ID changed from `f117185d-8202-427b-8499-afdbd00f3fe1` to
`7a87f82c-39ff-4691-b217-d249293832e6`, with uptime nine seconds.
The host recorded disconnect/reconnect at 18:26:58/18:27:08. DCDC5-off alone
does not prevent this transition failure; protective PMIC settings remain intact.
Evidence: `diagnostic-unplug-{before,after}.txt` and
`diagnostic-unplug-dcdc5-{before,after}.txt` under `build/sd-continuation/`.

### USB-to-battery transition: stock charge voltage

The extended physical PMIC read found CV register `0x64=0x03` (4.2 V),
battery ADC `0x10f3` (4339 mV) and IRQ status `0x4a=0x21`, including the
battery-overvoltage bit. Stock DT `pmu_init_chgvol=0x10fe` is 4350 mV.
The exact stock kernel's `axp2101_charger_probe` at `0x80532c50..0x80532cb0`
maps that value to selector 4, mask 7, register 0x64. An isolated emulator
run with erased user data confirmed `0x64=04` after kernel initialization;
stock's startup scripts/app left that selector unchanged. Emulator register
values are software evidence, not analog power qualification.

On physical V0.10, changing **only** register 0x64 from 03 to 04 changed
charger status 15 (not charging) to 14 (charge done) and IRQ 21 to 30:
the battery-overvoltage flag cleared without explicitly clearing interrupts.
DCDC5 remained enabled (80=1f). The device owner repeated ten seconds without USB or
headphones and reconnected without pressing Power. Boot ID remained
`7a87f82c-39ff-4691-b217-d249293832e6`; uptime progressed from 618.90 to
651.83 seconds, and USB re-enumerated in the same kernel. This isolates a
working correction for the reproduced transition failure. It does not yet
qualify prolonged battery playback or all earlier DAC-probe failures.

Patch 0043 supplies the stock 4.35 V limit through `simple-battery` and
`monitored-battery`; the RAM qualification init loads the power-supply drivers
to apply it before peripheral tests. Other charge settings and thermal/DC-DC
protections are unchanged. The new full image is being built; its automatic
initialization still needs a physical test. NAND V0.10 is unchanged.

Evidence under `build/sd-continuation/`: `power-pmic-v010-extended.txt`,
`stock-pmic-{kernel,app}-emulated.txt`, `stock-app-power-state.txt`,
`charger-cv/cv.c`, `power-pmic-stock-cv.txt`,
`diagnostic-unplug-cv-{before,after}.txt`, `player-stock-charge-build.log`.
The stock charger selector is also consistent with AXP2101 datasheet V1.4,
section 6.13.2.62. No battery capacity or runtime was measured.

### First audible mainline player on battery

The full #22 image with patch 0043 boots and exposes a 4,350,000 µV charge
limit through its battery power-supply driver. A controlled driver re-probe
started with CV selector 03 and changed it to 04 from the DT battery info,
verifying that startup does not merely inherit the corrected register value.
All four codecs, DRM and wheel registered; ALDO1 from patch 0042 allowed Wi-Fi
enumeration after MSC1 rebind. The generated WPA2/CCMP AP and Navidrome fixture
were reachable over Wi-Fi. Audio startup still used the earlier DCDC5-off seed
and temporary safe GPIO module; those are not yet a standalone boot solution.

The device owner unplugged USB and connected headphones. The same RAM boot stayed
reachable; PMIC USB online was 0 and battery status was Discharging. The player
requested `/rest/stream` from the generated Navidrome server and played the
fixture (subsequent starts reported Cached). ALSA reported RUNNING, S32_LE,
stereo, 44.1 kHz with advancing DMA/application pointers. DAC playback-volume
registers initially remained 78 (-60 dB). After all four stereo ALSA mixer
controls were set to 255 (0 dB), the device owner restarted the track and confirmed sound
in both ears. App volume remained independently limited. The device owner also confirmed
screen wake after idle. This establishes audible output and battery operation,
not long-duration runtime, analog fidelity or polished transitions.

The device owner reported pops when changing volume and requested comparison with stock.
The application volume integration and stock transition audit are in progress;
the independent charging review he requested is also pending. The old stock
I²S gate-reference leak was separately tested on this physical mainline boot:
100 unconfigured PCM open/close cycles returned audio, i2s1 and i2s1_gate
prepare/enable counts to zero after every close. After audible playback the
same gates were zero and hardware gate state reported disabled. No gates were
forced off and no battery-saving amount is inferred.

Evidence: `player-stock-charge-{boot,reprobe,probes,wifi-association,app}.txt`,
`player-stock-charge-wireless-monitor.txt`, `player-audible-battery.txt`,
`player-mixer-255.txt`, `i2s-mainline-{initial,100-cycles}.txt`,
`stock-audio-audit/findings.md`, and generated fixture `requests-live.json`
under `build/sd-continuation/`. Installed NAND remains V0.10.

### Integrated two-core RAM image and startup — 2026-11-02

Image #24, boot ID `9bd14125-eda2-4d8a-bca1-bf7d9b16a266`, booted from
cold-reset V0.10 with both CPUs online automatically. The ordered audio helper
registered all four DACs without `audio_safe.ko` and without first disabling
DCDC5. Display, buttons, wheel, gauge and beeper then registered through the
full helper; the player reached UI startup. This removes those two diagnostic
audio preparations from the successful startup sequence. The gauge reported
100 and the charger limit 4,350,000 µV. Wi-Fi associated after MSC1 rebind;
enumeration is asynchronous, so wait for `wlan0` before configuring it.

The preceding #22 volume candidate
`582728269669109a62acdfef9ffc7367023659d5ce734b2a55a6260fa4ca663b`
restored all DAC gains from a deliberately reset -60 dB to unity at startup.
The device owner confirmed no pops while adjusting volume during the generated tone.
That test does not qualify pause/start/stop transitions. Before the new boot,
CPU1 on #22 passed three offline/online cycles, each followed by 200,000
cross-core transfers of 256 words with release/acquire ordering and pinned
CPU affinity. No coherency mismatch or interrupt error was observed.

The #24 RTC initially had no valid readable time. `q2-clock-set` provisioned
UTC from the synchronized host and read back the same time. Persistence across
reset remains to test. The independent
[charger audit](../../docs/mainline-battery-audit.md) records verified settings
and remaining physical limits.

The first attempt to enter #24 from full #22 powered down/disconnected without
returning USB. Source review found the PMIC's registered power-off handler
precedes the old loader's legacy `pm_power_off` hook; this is a likely ordering
cause, not a captured execution trace. The device owner powered the device on, returning
to V0.10; its known RAM-loader path then booted #24. Native kexec from the full
profile remains a separate qualification requirement.

Artifacts: `build/os/player-hardware/artifact.json` and preserved
`build/worktrees/boot-chain/build/ram-handoff/player-hw9/`. Physical logs under
`build/sd-continuation/`: `player-hw9-from-v010.txt`,
`player-hw9-full-startup.txt`, `player-hw9-time-provision.txt`,
`player-volume-candidate-start.txt`, and `smp-hotplug-check.txt`.
