# Bluetooth headphones

Requirement: [BLUETOOTH-001](specification.md), the device owner's goal G4 of 2026-11-21
("pair headphones and listen on them"). Tracker:
[projects/bluetooth](../projects/bluetooth/STATUS.md).

**State (2026-11-22): built and host-tested. On the Q2, bring-up and scan
work (steps 0–2); pairing and audio have not run.** The device steps at the
end are the next work. Every hardware fact below
names its source; "unverified" means not observed on a Q2.

## Hardware path

| Fact | Value | Source |
| --- | --- | --- |
| Controller | Bluetooth side of the RTL8733BS combo (Realtek 8723F family, patch project id 19) | stock `/firmware/rtlbt/rtl8733bs_fw` trailer; [hardware.md](hardware.md#wi-fi-and-bluetooth) |
| Attachment | HCI over UART, three-wire (H5), 8 data bits, even parity | stock `demo`: `rtk_hciattach -n -s 115200 ttyS0 rtk_h5`; stock module `hci_uart_h5_kernel_5_10_21.ko` |
| UART | UART0 (`ttyS0`, 0x10030000). X2000 UART0 pins: PD23 RXD/PD24 TXD (`uart0-data`), PD25/PD26 CTS/RTS (`uart0-hwflow`) | mainline `pinctrl-ingenic.c` X2000 tables; the board routing is unverified |
| Initial rate | 115200 | stock command line |
| Working rate, stock | 3,000,000 with hardware flow control | stock `rtl8733bs_config`, UART entry `0x04928001`, flow bit set |
| Working rate, ours | 3,000,000 with RTS/CTS (rate code `0x04928001`, flow bit set in the configuration's UART entry, as stock's) where the kernel takes the rate; after a failed bring-up 1,500,000 with RTS/CTS (`0x04928002`), then 1,500,000 without | `[bluetooth] uart_mode = "auto"` ([UART fallback](#uart-fallback)). 3 M with RTS/CTS device-verified 2026-11-22 on kernel `pm-q2-perf-v134` with patch 0192, the line at 2.70 Mbaud ([result](#device-result-3-mbaud-works-2026-11-22-kernel-pm-q2-perf-v134-with-0192-ram-boot-over-v0135)); 1.5 M with and without RTS/CTS device-verified the same day. The mainline 8250 driver divides the 24 MHz UART clock by 16 × divisor: 1.5 M is exact (divisor 1); 3 M needs patches 0191 and 0192 (UMR 8) |
| Enable | BT_DIS_N = PD20, active high, shared supply chain with Wi-Fi | [hardware.md](hardware.md#wi-fi-and-bluetooth) |
| Reset | none separate; BT_DIS_N low resets the controller | stock module's power functions |
| Firmware | `rtl8733bs_fw` (57,020 bytes, SHA-256 `99603578…bbe8`): Realtek "epatch" v1, version `0xd5c81143`, patches for ROM versions 0 (12,337 bytes) and 2 (44,469 bytes). `rtl8733bs_config` (33 bytes, SHA-256 `e1bd37cb…7297`) | Shanling V1.32 update, parsed by `q2_bt::rtl` |
| Firmware licence | Realtek binary, shipped by Shanling. Not in linux-firmware (no `rtl8723fs` entry) and not redistributable from this repository. The image build copies the owner's extracted files and checks their hashes | `os/install/build.py --bluetooth-firmware` |

Unverified and decided by device step 1: whether UART0's pins are muxed at
boot (the mainline device tree assigned none), the ROM's HCI and LMP versions,
which ROM version the chip reports, and whether the controller accepts 1.5 M
without flow control (Realtek's rate code `0x04928002` is in Linux's `btrtl`
table for this family).

### What the stock firmware does

`module_driver/hci_uart_h5_kernel_5_10_21.ko` (Realtek's HCI UART line
discipline) is loaded at boot. To turn Bluetooth on, `demo` runs
`rtk_hciattach -n -s 115200 ttyS0 rtk_h5` (H5 sync, patch and configuration
download from `/firmware/rtlbt`, rate change), `bluetoothd` (BlueZ, over
`dbus-daemon`), `hciconfig hci0 up`, `sspmode 1`, `class 6c020c`, and
`bluealsa -c LDAC -c aptX-HD -c aptX -c AAC`; audio goes through
`usbdacbtplay plug:bluealsa`. Sizes on the stock root: `bluetoothd` 1,314 kB,
`dbus-daemon` 300 kB, `bluealsa` 217 kB, the two bluealsa ALSA plugins 85 kB,
`rtk_hciattach` 77 kB, plus glib, libdbus and libbluetooth.

## Kernel

The host stack runs in `q2nd` on the tty, so the kernel's Bluetooth subsystem
stays out.

| Option | Needed | Cost |
| --- | --- | --- |
| `CONFIG_BT`, `BT_HCIUART`, `BT_HCIUART_H5`, `BT_HCIUART_RTL`, `SERIAL_DEV_BUS` | no | 0 kB. For comparison the kernel route needs the HCI core, L2CAP, the management interface, H5 and `btrtl` (several hundred kB of unswappable text, not measured here), and `btrtl` has no 8723F entry in Linux 7.3 (its project-id table stops short of 19) |
| `CONFIG_SERIAL_8250_INGENIC` | already set | 0 |
| Device tree: UART0 pin groups | yes, unless step 1 shows the pins muxed | [patch 0181](../kernel/patches/0181-MIPS-Q2-mux-UART0-for-Bluetooth-and-add-a-Bluetooth-enable-hold.patch) |
| Device tree: `bt-en` regulator on PD20 with a userspace consumer, and `CONFIG_REGULATOR_USERSPACE_CONSUMER` | yes, to keep Bluetooth powered while Wi-Fi is off | one object, `drivers/regulator/userspace-consumer.c`, about 2 kB (estimate from its 220 lines; not measured) |

Powering Wi-Fi off unbinds MSC1, which releases `wlan-en` and with it
`wlan-bt-en`: BT_DIS_N drops and the controller resets. Patch 0181 describes
PD20 a second time as `bt-en`. Fixed regulators request their enable GPIO
non-exclusively and the regulator core counts enables per GPIO, so the line is
high while either regulator is on. `q2nd` writes `enabled` to
`/sys/devices/platform/bt-power/state` when Bluetooth starts and `disabled`
when it stops. Without the patch, `q2nd` refuses to start Bluetooth while the
Wi-Fi slot is unbound ("Turn Wi-Fi on to use Bluetooth"), and a Wi-Fi power-off
during listening ends the link.

`kernel/bluetooth.config` is the whole configuration delta. The kernel profile
pm-q2-perf-v126 (`kernel/q2_perf_v126.py`, V0.126's kernel) is pm-q2-perf-mem2
plus patch 0181 and `CONFIG_REGULATOR_USERSPACE_CONSUMER` (and patch 0184, the
scalar inflate defaults, unrelated to Bluetooth). A kernel-recipe build installs
the two firmware files in `/lib/firmware/rtlbt` with `tools/q2.py configure
--recipe pm-q2-perf-v126 --bluetooth-firmware DIR`, DIR being from
`tools/fetch_firmware.py` ([firmware record](bluetooth-firmware.md)) (the
composer checks their digests in `os/bluetooth_firmware.py` and refuses a kernel
without patch 0181) `os/install/build.py --bluetooth-firmware DIR` does the same
for a from-source install candidate. Recorded in `7643cd8d4` (2026-11-21).

## Stack

| | BlueZ + bluez-alsa | Kernel HCI/L2CAP + own profiles | Rust host in `q2nd` (chosen) |
| --- | --- | --- | --- |
| Processes | `dbus-daemon`, `bluetoothd`, `bluealsa`, `hciattach` | none extra | none extra |
| Memory | binaries about 1.9 MB plus glib/dbus libraries, three heaps; several MB resident (not measured on this image) | kernel text always resident when built in; modules possible | code inside `q2nd`'s locked image; one thread; buffers under 100 kB (PCM queue 70 kB at 44.1 kHz) |
| Kernel | `CONFIG_BT` and friends, `btrtl` patch for the 8723F | same as BlueZ | none |
| Maturity | highest; years of interoperability fixes | lower layers mature, profiles new | all new: H5, HCI, L2CAP, AVDTP and AVRCP written here |
| Licence | GPL-2.0 (BlueZ), MIT (bluez-alsa) | GPL-2.0 kernel | this repository's |
| Host tests | need a kernel with `vhci` | need a kernel | complete: a fake controller and headphones in-process |

Decision: the Rust host. It costs no kernel memory, no daemon and no D-Bus,
ships as an app update, and every layer runs in host tests against a fake
controller. Its risk is interoperability: it has met few real devices.

BTstack was considered and is excluded: its licence allows non-commercial
use only, which the owner rejects (the device owner, 2026-11-21: "BTstack is not an
option — the noncommercial clause is a dealbreaker"). If a class of
headphones cannot be served and the fault is in the lower layers, the
fallback is the kernel's HCI and L2CAP (GPL-2.0) under the same profiles,
PCM boundary (`bluetooth::pcm`) and Settings page.

What the host implements (`crates/bt`, no `unsafe`, no I/O):

- **H5** (`h5.rs`): SLIP framing, link establishment, sequence numbers,
  acknowledgement, retransmission after 250 ms during bring-up and 100 ms at
  the fast rate, window 4. No CRC is requested.
- **Bring-up** (`host.rs`, `rtl.rs`): H5 sync at 115200; read local version;
  read ROM version; vendor rate change to the UART setting's rate (3 M in
  `auto` mode); download of the patch for that
  ROM version plus the configuration file with its UART entry rewritten to the
  rate and flow setting in use; then reset, buffer sizes, address, event mask,
  Secure Simple Pairing on, class of device `0x28041c`, name `Q2`, page scan on,
  inquiry scan off. A controller that kept its patch (the player restarted)
  answers only at the fast rate; the host tries both. After a failed bring-up
  the sync also tries the failed attempt's setting, and moves a controller
  found there to the new rate ([UART fallback](#uart-fallback)).
- **Pairing**: Secure Simple Pairing with no input and no output ("Just
  Works"), general bonding; PIN `0000` for headphones without it. Link keys go
  to `/state/bluetooth/devices` (mode 0600). A refused key is dropped and the
  screen asks to pair again.
- **Devices of another class** (a computer, a speaker that is not class
  audio/video): `Command::ConnectSink` opens the ACL link unauthenticated,
  opens an SDP channel and sends one ServiceSearchRequest for the Audio Sink
  UUID 0x110b. One or more records: the channel is closed and pairing goes on
  as for headphones. No record: `Event::NotSink`, the link is closed and
  nothing is paired. No answer in 10 s, an error response or a refused
  channel: the link is closed unpaired. Once paired, such a device reconnects
  as headphones do, without the check.
- **Inquiry results**: the class, RSSI and name, and whether the extended
  inquiry response's 16- or 128-bit UUID lists name the Audio Sink (`sink` in
  `Event::Found`).
- **One ACL link**, authenticated and encrypted before any audio channel
  (the sink check's SDP channel is the one exception).
  Incoming connections are accepted only from paired headphones.
- **L2CAP** basic mode, fixed 672-byte receive MTU, the peer's MTU for sending.
- **SDP server**: three fixed records: A2DP source 1.3, AVRCP target 1.4
  (category 1, player) and AVRCP controller 1.4 (category 2, amplifier),
  both over AVCTP 1.3. **SDP client**: the service search above, and once per
  connection, 3 s after the stream opens, the headphones' Device ID record
  (vendor and product, for the [EQ match](#headphone-eq-per-output)).
- **AVDTP**: one SBC source endpoint. Initiator when we connect; acceptor when
  the headphones reconnect and configure the stream themselves, with a fallback
  to initiating after 1.5 s.
- **SBC** (`sbc.rs`): 8 subbands, joint stereo or stereo, loudness or SNR,
  bitpool up to 53 (328 kbit/s at 44.1 kHz). The other codecs are in
  [Codecs](#codecs).
- **AVRCP** 1.4. Target: play, pause, stop, next, previous, volume up and
  down (pass-through), playback status. Controller: the headphones' absolute
  volume ([Volume](#volume)).

## Codecs

Requested by the device owner, 2026-11-21: "make sure we have LDAC, aptX-HD, aptX, and
AAC bluetooth support", with no non-commercial licence terms. **Host-tested
only: none of these four has been sent to real headphones.** The device
record above this point was all SBC-capable code; nothing below it is.

| Codec | Encoder | Licence | Arithmetic | Bit rate at 44.1 kHz | Notes |
| --- | --- | --- | --- | --- | --- |
| SBC | `crates/bt/src/sbc.rs`, written here | this repository's | f64 | 328 kbit/s (bitpool 53) | mandatory; always the fallback |
| aptX | libopenaptx 0.2.0, `crates/codecs/q2-aptx` | LGPL-2.1-or-later | integer | 352.8 kbit/s | 0.2.1 and later are GPL-3.0-or-later and are not used |
| aptX HD | same | LGPL-2.1-or-later | integer | 529.2 kbit/s | 16-bit input in the top of 24 bits |
| LDAC | AOSP libldac 2.0.2 (commit `eeee1a3f`), `crates/codecs/q2-ldac` | Apache-2.0 | single float (an integer build exists behind a feature) | 909 / 606 / 303 kbit/s (990 / 660 / 330 at 48 kHz) | encoder only; needs a 679-byte media MTU |
| AAC-LC | vo-aacenc (commit `a277487e`), `crates/codecs/q2-aac` | Apache-2.0 | integer | 256 kbit/s constant, up to 320 | LATM, one access unit per element |

Each crate's `README.q2.md` has the fetched version, hashes, the licence
evidence and how to verify it. The crates' FFI is the only `unsafe` on the
codec path: 19 sites (3 extern blocks, 3 `unsafe impl Send`, 13 blocks), all
in each crate's `src/lib.rs`, each with a SAFETY comment, run by
`cargo test --workspace`. The async inventory does not scan `crates/codecs`;
the count is recorded in `docs/async/budget.json` (`codecs_ffi_unsafe`).
Points the owner should read:

- **LGPL (aptX, aptX HD)**: the encoder is linked statically into `q2nd`.
  LGPL-2.1 section 6 then requires that a recipient of the binary can relink
  it with a changed library. With this repository's source available to
  whoever gets the binary, that holds.
- **aptX patents**: EP0398973B1 (the aptX algorithm, priority 1988) is listed
  as expired or revoked. Not verified: that it is the only aptX patent, any
  patent on aptX HD (none was found and none ruled out), and any statement
  from Qualcomm. "aptX" is a Qualcomm trademark with its own certification
  terms, not examined.
- **LDAC**: the code is Apache-2.0 (Sony copyright), with a patent grant from
  its contributors. Its `NOTICE` file says "Taking the certification process is
  required to use LDAC in your products" and points at Sony. Under Apache-2.0
  a NOTICE is informational and does not change the licence; what Sony's
  programme requires of a non-Android device, and its terms, are behind a
  login and were not read. Sony's own page says the source (encoder) side was
  given to AOSP free and the sink side needs a licence. The LDAC name is a
  Sony trademark.
- **AAC**: vo-aacenc ships "THIS IS NOT A GRANT OF PATENT RIGHTS". The Via
  Licensing pool still lists AAC-LC and says a licence is needed by makers of
  end-user encoder or decoder products; whether any listed patent is still in
  force for AAC-LC, and whether the owner's use needs a licence, were not
  established. Considered and not used: fdk-aac (its own licence: no
  non-commercial clause, but "only for purposes that are authorized by
  appropriate patent licenses", and Fedora classes it not-allowed), FFmpeg's
  `aac` (LGPL-2.1+, floating point, not separable from libavcodec), FAAC.
- **AAC quality**: vo-aacenc fills 13–22 % of a 256–320 kbit/s stream with
  padding and measures below FFmpeg's and FDK's encoders at those rates
  (README table). For that reason Automatic does not choose AAC.

### Negotiation

`crates/bt/src/codec.rs` holds each codec's capability bytes, the choice from
a sink's offer and the configuration sent. The source has one stream endpoint
per codec (SEID 1 SBC, 2 AAC, 3 aptX, 4 aptX HD, 5 LDAC).

- **We initiate** (first pairing, or a connect from Settings): discover the
  sink's endpoints, read the capabilities of each, and configure the first
  codec of the preference that the sink offers and we can serve.
- **The headphones initiate** (their reconnect): they pick one of our
  endpoints; any codec this build has is accepted when the configuration is
  within what we offered. When the media channel opens, before the stream is
  reported to the player, the host discovers their endpoints and reads each
  one's capabilities once per link. If they offer a codec the preference
  ranks above theirs, it closes their stream (AVDTP Close, then the media
  channel) and configures the better one; if they refuse that configuration
  it configures theirs again, and if they refuse the Close, do not answer
  within 4 s, or start the stream first, their stream stays. The player sees
  one stream, the final one.
- **Preference** (`[bluetooth] codec`, Settings › Bluetooth › Preferred
  Codec): `auto` is LDAC, aptX HD, aptX, AAC, then SBC; a named codec is that
  codec, then SBC. A change applies at the next connection. The device owner,
  2026-11-22 (V0.133): "Auto connection mode should select the highest
  quality option by default ... I had to select AAC and reconnect to get AAC
  on my airpod pro 2s." Cause: `auto` ranked AAC after SBC (an earlier agent
  choice: the vendored AAC encoder measured below SBC at bitpool 53), so
  headphones with SBC and AAC got SBC. AAC is now above SBC.
- **Why**: the device page and Bluetooth Details show how the codec was
  chosen: the best both sides have, Chosen in Settings, the headphones lack
  the chosen one, chosen by the headphones (with nothing better in common),
  or changed from the one the headphones chose.
- **Rates**: 44.1 kHz when the sink offers it, else 48 kHz, for every codec.
  LDAC at 88.2 and 96 kHz is not offered. `ldac_quality` is `standard`
  (606/660 kbit/s) by default: `high` is 909/990 kbit/s, 87–95 % of the
  1.5 Mbaud line and 44–48 % of 3 Mbaud ([UART budget](#uart-budget)), and is
  untested on the Q2.
- **Framing**: SBC and LDAC put a frame count byte after the RTP header; AAC
  is a LATM element, fragmented to the MTU with the RTP marker on the last
  fragment; aptX HD is bare codewords after the RTP header; aptX has no RTP
  header.
- A sink whose media MTU is under 679 bytes cannot take LDAC from libldac:
  the encoder refuses, the link is closed with a message, and the listener
  picks another codec.

### Encoder cost

`q2nd --bt-probe bench [--seconds N]` encodes generated 44.1 kHz stereo with
each codec as a stream would, with no controller.

| Codec | Host x86-64, ms per audio second |
| --- | --- |
| SBC | 1.4 |
| aptX | 9.2 |
| aptX HD | 9.7 |
| AAC 256 kbit/s | 8.7 |
| LDAC 303 / 606 / 909 kbit/s | 2.1 / 2.2 / 2.4 |

Under qemu-user (`-cpu P5600`, the Q2 release build `bluetooth-codecs-01` of
commit `57a7a657`, 10 s of audio), ms per audio second:

| Codec | qemu-user |
| --- | --- |
| SBC | 79.7 |
| aptX | 40.0 |
| aptX HD | 42.0 |
| AAC 256 kbit/s | 53.2 |
| LDAC 303 / 606 / 909 kbit/s | 59.5 / 61.0 / 61.7 |

Emulator time is not Q2 time: qemu-user computes floating point in software,
which inflates SBC (f64) and LDAC (float) against the integer codecs, and its
speed is the host's. The figures say only that no encoder is far from real
time. The Q2 figure comes from the same command on the device:

```sh
q2nd --bt-probe bench            # 30 s of audio per codec; about a minute in all if near these figures
```

`cpu_percent` there is the share of one core while that codec plays. If
LDAC's float path is slow on the X2000, `crates/codecs/q2-ldac` has a
`fixed-point` feature (libldac's integer build) to compare. The encoders are
C built by `cc` with `-O2`; the build that uses the X2000 toolchain
(`Q2_X2000=1`, on master, not on this branch) would need its MSA flags
passed to `cc` (`CFLAGS_mipsel_q2_os_linux_gnu`) for them to be vectorized.
The release binary grows by 197 kB with the three encoders (16,276,472 to
16,473,812 bytes); it is locked in memory.

With headphones that have the codec, after pairing:

```sh
q2nd --bt-probe play 20 --codec ldac                        # or aptx-hd, aptx, aac, sbc
q2nd --bt-probe play 20 --codec ldac --ldac-quality high    # 909/990 kbit/s over the 1.5 M UART
```

Pass: the tone is heard, the `stream codec=` line names the codec asked for
(SBC means the headphones do not offer it), `dropped_packets=0` and
`underruns=0`.

### Codec tests

- Each encoder crate: stream hashes for generated input, rate and framing
  checks, and an environment-gated dump for an external decoder.
  Decoded once during development: aptX and aptX HD by FFmpeg (42.3 / 39.5 dB
  and 52.7 / 50.0 dB left / right, delay 90 samples); AAC by FFmpeg (delay
  1,600 samples, 430 access units to 440,320 frames; 32–34 dB left at
  256–320 kbit/s on the test signal); LDAC by hegdi/libldacdec (MIT, a
  development check only, not vendored; 67 / 46 / 42 dB at High / Standard /
  Mobile, delay 128). The test signal carries broadband noise, so these
  figures describe that signal.
- `crates/bt`: capability bytes as other implementations write them; the
  best-codec matrix against fake headphones offering each set, the override,
  and SBC as the fallback; each codec's packet framing on the wire; headphones
  that reconnect and configure each codec themselves; a build without a codec
  refusing it.
- `crates/q2nd`: packets within the MTU and every sample accounted for, AAC
  fragments reassembling to one LATM element per access unit, stated bit
  rates, and each setting negotiated, encoded and paced through the thread to
  the fake headphones.

## Audio route

```text
Decode → EQ + ReplayGain → software volume → sink ─ ALSA hw:Q2,0 (wired)
                                                  ├ ALSA plughw (USB DAC)
                                                  └ q2bt → resample → SBC → AVDTP
```

- `audio::output::Route::Bluetooth` is selected while the headphones' stream is
  open and takes precedence over USB and the jack. The player opens the PCM
  named `q2bt` (`audio/pcm_backend.rs`), implemented in process by
  `bluetooth::pcm`. It follows ALSA's contract as the sink uses it: a 400 ms
  queue, start at half full, blocking writes, `-EPIPE` after an underrun,
  delay in source frames, no hardware pause (the sink keeps its unsent tail
  and drops, as for any device without one). EQ, ReplayGain and the retained
  PCM rules therefore apply unchanged.
- **Volume**: one value, the player's, saved per device; with absolute
  volume it is the headphones' amplifier and the samples stay at unity
  ([Volume](#volume)).
- **Format**: the stream is 44.1 kHz when the headphones offer it, else
  48 kHz. Other source rates pass through a polyphase Kaiser-windowed sinc
  resampler (`resample.rs`); mono is doubled. The stream is not reconfigured
  per track.
- **Latency**: up to 400 ms in the PCM queue (as on the other outputs), one
  media packet (20 ms at 7 frames), and the headphones' own buffer, typically
  100–250 ms. The delay the player sees covers frames not yet sent, so the
  position display runs ahead by the headphones' buffer. Late media packets
  are dropped, oldest first, when the controller stops taking them.
- **Connect** while playing: playback moves to the headphones and continues.
- **Disconnect** (Settings, headphones switched off or out of range): the
  route returns to the wired choice and playback pauses (`refresh_route`),
  with no error and no Last Problem. The audio not yet sent is kept and plays
  when the listener presses Play. The thread changes the route before it fails
  writes, so a write the disconnect fails ("Bluetooth headphones
  disconnected") is taken as the pause. A write that stalls first ("Bluetooth
  headphones stopped taking audio") is shown until the link drops, which
  clears it. A stream that closes after it opened leaves no Last Problem; one
  the headphones never opened leaves "These headphones did not accept audio."
  V0.128 paused a switch-off with the write's error and left that Last
  Problem (the device owner, 2026-11-22); fixed after it (host-tested; not yet run on a Q2).
- **Pause**: the stream is suspended after 2 s without audio and started again
  on the next write, so seeks and track changes do not toggle it.
- **Switching off** in Settings does not wait: the thread disconnects and
  closes the port on its own, and a new start waits for it.

## Volume

Requirement: BLUETOOTH-002 in [specification.md](specification.md). The device owner,
2026-11-22, on V0.133 with headphones that offer LDAC: "The headphone volume
up/down buttons ... do not work. The forward/prev controls work correctly";
"BT volume is separate from the scroll wheel volume. Please bring these two
together ... We should also save volume per-BT device"; "Volume's maxed out
and they're maybe 10% volume ... I've tried turning EQ and volume
normalization off."

**Cause, from the code of V0.133** (commit `8c78f460`; no device log was
taken). The player never set the headphones' amplifier and ignored their
volume keys, so the headphones stayed at their own level:

- `crates/bt/src/avrcp.rs:2`: "Volume stays local to the headphones; absolute
  volume is not offered."
- `avrcp.rs:36-40`: pass-through operations other than play, pause, stop,
  next and previous were answered NOT IMPLEMENTED, so VOLUME UP (0x41) and
  VOLUME DOWN (0x42) from the headphones did nothing.
- `avrcp.rs:55-58`: RegisterNotification was served for playback status only;
  the volume event and SetAbsoluteVolume were rejected.
- `crates/bt/src/sdp.rs:35,41`: one AVRCP record, target 1.3 over AVCTP 1.2,
  category 1. No controller record, so headphones could not see a source that
  sets absolute volume (AVRCP 1.4).
- `crates/bt/src/host.rs:365`: the control channel only answered the
  headphones; the player sent no AVRCP command (no GetCapabilities,
  RegisterNotification or SetAbsoluteVolume).
- The player's own path added no attenuation at full volume: software gain is
  `soft_gain(100)` = 1.0 (`audio/player/output.rs:359`), the Bluetooth PCM sends
  the top 16 bits of full-scale samples (`bluetooth/pcm.rs`), LDAC is fed S16
  (`q2-ldac`), and `[playback] max_volume` defaults to 100. So "maxed out" was
  full scale into an amplifier the player never raised.

**Roles.** For absolute volume the source is the AVRCP controller and the
headphones are the target of category 2: the player sends GetCapabilities,
RegisterNotification(VOLUME_CHANGED) and SetAbsoluteVolume to them, and they
notify their changes. The player is also a category 1 target for their
buttons. Some headphones register for volume at the source's target or send
SetAbsoluteVolume to it; those are answered too (interim, then changed;
accepted), although the player's target does not list the volume event in its
capabilities.

**Absolute volume** (`host.rs` `Remote`, `avrcp.rs`):

1. When the control channel opens (by either side), GetCapabilities(events).
2. If the volume event (0x0d) is listed, RegisterNotification for it. The
   interim response marks the headphones as taking absolute volume
   (`Event::AbsoluteVolume`) and reports their level (`Event::Volume`, initial).
3. At that first report: a volume saved for the device is sent with
   SetAbsoluteVolume; without one the player takes the headphones' level.
4. Player changes (wheel, headset keys) go out as SetAbsoluteVolume, one
   command at a time; changes made while one waits are merged into the latest
   level, sent when the response arrives or after 2 s without one. A level the
   headphones already hold is not sent again.
5. A CHANGED notification (the headphones' buttons) is reported as theirs
   and the registration is renewed. A CHANGED that echoes a level the player is
   setting, or a rounding of it, is not reported back.

Levels: player volume 0..=100 ↔ AVRCP 0..=127, `to_level` and `from_level` in
`bluetooth/mod.rs`; every player volume survives the round trip.

**One volume.** While absolute volume is active on the Bluetooth route,
`Cmd::SetRemoteVolume(true)` puts the player's samples at unity (volume 0
still mutes; EQ and ReplayGain headroom still apply). The wheel and the
headphones change the same value; full volume is the headphones' full volume.
The on-screen volume follows the headphones' buttons and is shown as it
changes. The device owner's 2026-11-24 correction makes Settings › Volume Limit apply to
Bluetooth when pairing, reconnecting, turning the wheel or using the headphones'
own controls. Lowering the limit turns down an active output above it. A quieter
pair of headphones keeps its level even if the jack was louder. Raising the
limit preserves the listening level.

The player clamps each device's saved level before reconnecting. A headphone
report above the ceiling gets a corrective AVRCP set. The player uses software
attenuation until the headphones report an amplifier level within the ceiling.
A quieter saved level on reconnect also gates this handoff. Headphones that
round a requested setting upward past that boundary keep software attenuation.

**Fallback.** Headphones that do not list the volume event keep software
volume (0.5 dB a step, as on a USB DAC). Their volume keys, when they send
pass-through VOLUME UP/DOWN, step the player's volume by 4 (2 dB). Headphones
that change only their own amplifier on a key press, without telling the
source, keep that level separate; nothing the player can see changes.

**Per device.** The wired jack keeps `[playback] volume`. Each paired device
keeps its own volume in `/state/bluetooth/devices` (`volume <address> <0-100>`),
written 2 s after the last change and when the link drops. On connect the
saved volume is restored (and sent, with absolute volume). A device seen for
the first time starts at the jack's volume capped at 60, or at the
headphones' own level when they take absolute volume. The address is the
record's key in the file and is not logged.

## Headphone EQ per output

Requirement: EQ-OUTPUT-001 in [specification.md](specification.md). The device owner,
2026-11-22: "pin the 3.5mm to a user selected one, but when connecting eg
airpods vs sony's, we should be able to auto switch to the correct autoeq
profile."

- The graphic EQ (bands, presets) is the listener's taste and is the same on
  every output.
- **Jack**: the profile chosen in Settings › Sound › Equalizer while no
  Bluetooth output is in use, kept in `[playback.eq]` as before ("Pinned").
- **Each Bluetooth device**: its own profile, kept in the device store
  (`eq <address> auto|chosen on|off <catalog path> <name>`). It never uses the
  jack's profile.
- **Automatic match** at the first connection, against the packaged AutoEQ
  catalog (`assets/autoeq/index.tsv`, 6,037 models) by
  `audio/eq_match.rs`: the device name without an owner's possessive
  ("Alex's AirPods Pro") or a `LE_` prefix; the brand from the Device ID
  vendor (Bluetooth SIG or USB IDs of Apple, Sony, Samsung, Google, Bose) or
  from the name; Apple's AirPods product IDs for renamed pairs (from public
  reports, unverified on a Q2); and the class of device (speakers, cars,
  computers get no profile). The model must agree once spaces, punctuation and
  filler words are gone ("WH-1000XM5" is "Sony WH-1000XM5"), or every word of
  the model must be in the name with no model number missing. Among equal
  models the plain measurement wins over variants (ANC modes, ear tips), then
  oratory1990, crinacle, Rtings, Innerfidelity. No match: no profile, and a
  Device ID read later tries again.
- The matched profile is read from the card's AutoEQ cache, or downloaded once
  (as Choose Headphones does); offline and not cached, the page says "Not
  downloaded" and it is tried again when Wi-Fi returns. A toast names the
  profile when a match is applied.
- **Switching**: the player's EQ changes with the route: to the device's
  profile when its stream opens, back to the jack's pin when it closes.
- **UI** (Settings › Sound › Equalizer, also from Now Playing): an Output
  row ("Headphone Jack" or the device's name); Headphone EQ On/Off for that
  output; Choose Headphones (for the jack it pins, for a device it remembers
  the choice for that device); the profile row with why it is in use
  ("Pinned", "Matched automatically", "Chosen", "Finding a match…", "No
  automatic match", "Not downloaded"); and Match Automatically, which forgets
  a device's choice and matches again.

## Settings

Settings › Bluetooth, a top-level Settings item after Connections (the device owner,
2026-11-22: "split this out to a top level settings item"). About moved under
System in the same change. Screens (`ui/screens/bluetooth.rs`):

- **Bluetooth**: the switch, with the controller's state under it
  (`Starting…`, `Ready`, or why it is unavailable, then Try Again, which
  starts the controller again without switching off); Last Problem and Try
  Again (the same device, the same way) after a failed attempt; the paired
  devices, each with its state (Playing or Connected with the codec and its
  bit rate, the step of an attempt, or when it was last connected); Pair New
  Device; Preferred Codec (a choice list). Paired devices and Pair New Device
  are offered while the controller starts: their requests wait for it.
- **A paired device**: Connect, Disconnect or Stop Connecting; Status; Codec
  (bit rate, LDAC's quality mode, sampling rate) while connected, otherwise
  Last Connected; Type; Forget This Device… with a confirmation. Connecting
  one device disconnects another first.
- **Pair New Device**: a search that runs while the page is open (inquiry
  rounds for up to two minutes; see below), the devices found with their
  signal (Strong, Good, Weak, as on the Wi-Fi page) and type (Headphones,
  Speaker, Audio device, Takes Bluetooth audio, Other device), headphones and
  speakers first and stronger first. Choosing one shows "Pairing with …" and
  its step (Connecting…, Checking for Bluetooth audio…, Pairing…, Securing
  the connection…); success closes the page with "Connected to …"; a failure
  shows why in words with Try Again. Back stops the search. Opening the page
  ends a connection attempt in progress to another device, which is not
  resumed ([below](#requests-timeouts-and-retries)). An audio-class
  device pairs at once; any other is paired only after the SDP check above,
  and one without an A2DP sink is refused with "This device does not take
  Bluetooth audio (no A2DP sink). It was not paired."
- **Bluetooth Details** (long Center on the Bluetooth page): controller
  state, firmware (patch loaded or kept, ROM, revision and subversion), link
  and pairing step with RSSI and link quality while audio streams, codec, the
  session's last error, H5 and command counters, scan counts, dropped packets,
  underruns and credit stalls, the checks after Wi-Fi power-ups, and requests
  waiting. The H5, RSSI, link-quality and credit-stall numbers are the
  `State::link_stats` copy telemetry publishes every 250 ms. No addresses and
  no device names.

`last connected` is stored as `seen <address> <seconds since 1970>` records in
`/state/bluetooth/devices`; files written by earlier builds read without them.

### Requests, timeouts and retries

The device owner, V0.133: "I had to toggle bt on and off a few times to get a pair of
headphones to pair. Seemed like maybe bt wasn't fully up and running when I
first tried to pair." From the code (no device log was taken), three ways a
request could be lost with the screen left searching or connecting:

1. A scan or connection asked before the controller was ready reached a host
   that drops commands until Ready, after the thread had set `scanning`.
   Fixed by 3848cecf (requests wait for Ready) and by the hold below.
2. At Ready the thread pages the last headphones (5.12 s when they are off).
   A scan or connection asked then was refused by the host (`connecting`
   set) and dropped, again with `scanning` set.
3. Nothing bounded an attempt the controller never finished: no Connection
   Complete, or pairing that stalls, kept `connecting` or an unencrypted link
   that refused every later scan and connection until a restart.

What the thread and host do now:

- A scan or connection the host cannot start yet waits in order, up to 45 s,
  then leaves "Bluetooth was busy with another connection. Try again." A new
  connection replaces one still waiting; Disconnect ends a waiting one, and
  Forget ends a waiting one to that device. The
  automatic reconnect at Ready gives way to it (HCI Create Connection
  Cancel) and is not resumed, and is not started when the listener has already asked for a scan,
  a connection or a search. `scanning` follows the host, so no refused
  request leaves it set.
- Each step is bounded: 12 s from Create Connection to Connection Complete
  (the page is cancelled), 10 s for the SDP check, 30 s from the link to
  encryption, counted again after a passed SDP check (authentication or
  pairing; the link is closed and forgotten after 3 s if the controller does
  not confirm). Each ends with a message naming what did not happen. After a
  page timeout a new attempt starts at once; after the others it waits until
  the link is closed (at most 3 s). A page that completes after its cancel was
  sent is closed. A scan that stops for a connection also cancels its name
  request, which pages the device it names.
- the device owner, V0.133: "BT scan doesn't work if it is currently trying to connect
  to a pair of headphones." A scan or Pair New Device during the reconnect
  at start cancels that page at once and searches (Pair New Device shows
  Searching… at once).
- the device owner, 2026-11-22: "If the user has just paired a new device, they
  absolutely do not want to resume connecting to the other device. Cancel
  connections in progress when pairing and don't come back to them."
  Entering Pair New Device ends any connection attempt in progress: the
  reconnect at start or one the listener asked for (a page is cancelled, a
  link not yet encrypted is closed), with no message. Connections still
  waiting from before it are dropped (`drop_connects_before_search`). A
  connected device stays connected. Choosing a device to pair cancels the
  reconnect page as any connection does. Nothing pages the old device again:
  not when the search ends, not after the page is left without pairing. A
  paired device is the connected one and the last-used one, so the next
  Bluetooth start reconnects it; after leaving without pairing, the next
  start reconnects the last-used device as before. A scan (`--bt-probe`)
  that cancelled the reconnect does not resume it either.
- Pairing is Secure Simple Pairing with IO capability NoInputNoOutput, no
  out-of-band data, general bonding without MITM: Just Works, with the
  numeric comparison accepted. Passkey and out-of-band requests, which a
  Just Works pairing should not produce, are refused at once instead of left
  to stall. Legacy PIN `0000` is unchanged.
- Pair New Device searches in rounds (`Bluetooth::discover`): one inquiry
  after another with a 1 s pause, for two minutes or until the page closes,
  and not while a device is connected (an inquiry takes air time from audio),
  so headphones put in pairing mode late, or missed by one inquiry (after a
  fresh download the first inquiry has found nothing on the Q2; see the
  device steps), still appear. A connection attempt pauses the search; names
  learned by one round are not asked for again.
- A failed start shows Try Again, which starts the controller again.

Host-tested only. The 45 s, 12 s and 30 s limits are choices, not measured
on the Q2.

`[bluetooth]` in `config.toml`: `enabled`, `device` (`/dev/ttyS0`, or `fake`),
`uart_mode` (`auto`: 3 Mbaud with RTS/CTS, then 1.5 Mbaud with RTS/CTS, then
without; `manual`: `baud` and `flow_control`, then 1.5 Mbaud without flow
control; [below](#uart-fallback)), `baud` (1500000),
`flow_control` (false), `firmware_dir`, `codec`, `ldac_quality` (the highest
LDAC rate), `ldac_adaptive` (true), `resync_after_wifi` (true; below). Firmware is
looked for in `firmware_dir`, `/lib/firmware/rtlbt`, `/state/bluetooth/firmware`.
The simulator and headless runs use the fake controller with two fixture
headphones and a fixture computer (class 0x7c0104, an A2DP sink with SBC and
AAC in its SDP records, no UUIDs in its inquiry response).

### After the Wi-Fi chip powers up again

The radio doze, Wi-Fi Off/On and Offline Mode power the combo chip down and
up through the network helper. Each helper `up` that follows a `down` is
passed to the Bluetooth thread, which asks the controller for its version
(3 s):

- no answer: the controller lost power with the Wi-Fi side (the hold did not
  keep BT_DIS_N up); the thread brings it up again, as a start does;
- an answer with no headphones connected, connecting or scanning: it runs
  the start-up commands again (HCI Reset and the configuration; no download,
  no page to the last headphones), so the controller starts over beside the
  new Wi-Fi driver instance. `resync_after_wifi = false` turns this off;
- an answer with headphones connected: nothing; the link and the stream go
  on through the doze.

Why: after a helper cycle with Bluetooth on, Wi-Fi stayed out of LPS for
10+ minutes until a player restart
([bug](bugs/wifi-lps-after-power-cycle.md)); whether the re-run is what
cleared it is decided on the device there. `/run/q2/bluetooth-status` holds
one line for device diagnosis: phase, link, codec, the check counts and H5
counters, no addresses or names. Host-tested only.

## Tests

- `crates/bt`: 53 tests. SBC: frame lengths, a decoder written from the
  synthesis side of the specification (30 dB or better at bitpool 53), and
  three stream hashes. FFmpeg's SBC decoder decoded those three streams at the
  expected 73-sample delay with 39.2 dB (joint, bitpool 53), 32.3 dB (stereo,
  48 kHz, bitpool 51) and 31.1 dB (SNR allocation, bitpool 35) against the
  input. H5 through loss and corruption. Realtek files, including the stock
  files when `Q2_BT_FIRMWARE_DIR` points at them. Resampler pass-band and
  alias rejection for 13 rate pairs. The host against the fake controller:
  bring-up with download and rate change, a kept patch, a controller left at
  a failed attempt's setting and moved to the new rate, a silent UART, scan,
  Just Works and PIN pairing, the stream and its audio, reconnect from either
  side, refused keys, restricted sinks, a hung sink, buttons, SDP, dropped
  late audio, and random input. A computer of class 0x7c0104: the SDP search
  runs before any pairing command, then pairing, an SBC stream and its audio,
  and a later plain reconnect; one without a sink record is refused unpaired;
  one whose SDP never answers is closed unpaired after 10 s; the Audio Sink
  UUID in the inquiry response is reported. An inquiry never reported
  complete ends the scan at 14 s with the devices it found.
- Volume and EQ per output, host tests (2026-11-22): `crates/bt` AVRCP PDUs
  (SetAbsoluteVolume, RegisterNotification interim and changed, GetCapabilities,
  pass-through volume, refusals, random input) and, against the fake
  headphones, absolute volume set and followed both ways, merged quick
  changes, an echoed or rounded level not reported, headphones without absolute
  volume, headphones that ask the player for its volume, and the Device ID
  query; the SDP records and the Device ID request and parse. `crates/q2nd`:
  the level mapping, the store's new records, the thread adopting, saving and
  restoring a device's volume and EQ choice through a reconnect, the player at
  unity with absolute volume and at the 0.5 dB curve without it (and the jack
  unaffected), the matcher on synthetic names and on the packaged catalog,
  the per-output state (first-connection cap, following the headphones once,
  EQ switching on route change, stale results), the App (wheel and headset
  keys change the device's volume and not the jack's), and the Equalizer page
  on Bluetooth.
- `crates/q2nd`: the PCM endpoint, the tty settings on a pseudo-terminal, the
  thread against the fake (pairing, key file mode, 48 kHz mono through the
  resampler, pacing, suspend, a restart that waits for its predecessor, the
  fixture computer through the SDP check to an AAC stream, headphones switched
  off mid-stream: the write fails with ENODEV and no Last Problem is left),
  the sink on the `q2bt` PCM, the route policy, player tests (connect moves
  playback, disconnect pauses, Play resumes with no audio lost; a write that
  the disconnect fails, or that stalled before it, ends paused with no
  error), the page's rows, and the probe's choice of device (default and
  `--any`). Requests and retries: the reproduction of the device owner's V0.133 report
  (switch on, then scan and pair at once, with a bring-up slowed to about a
  second) fails with the old request handling and passes; a scan during the
  reconnect at start ends that page and finds the devices (fails with only
  3848cecf) and the page is not resumed; Pair New Device ends the reconnect
  at start or a connection the listener asked for, drops a connection still
  waiting, and pages nothing after it is left, while the next start
  reconnects the last-used device; a device paired during the reconnect at
  start stays connected and is the one the next start reconnects; a failed attempt leaves its notice and Try Again pairs; a failed
  start is retried without switching off; discovery finds headphones switched
  on late and stops when asked; codec bit rates. The pages' rows in every
  controller, link and pairing state; Settings › Bluetooth and System ›
  About; Pair New Device through to a connected device that closes the page,
  then its page's Disconnect and Forget.
- `crates/bt` (additions): a pairing that stalls times out at 30 s, closes the
  link, and an immediate retry pairs; a page with no answer is cancelled at
  12 s; Create Connection Cancel ends a page at once; a later scan reuses the
  names it learned.
- Headless: `--script` through Settings › Bluetooth, Pair New Device, a device
  page, Bluetooth Details and Preferred Codec. The simulator's fake runs at
  about a real controller's pace (a start of about a second, page and pairing
  steps of 0.7 s) so each state can be seen; tests run it at full speed.
  Recorded in `0d5075ec0` (2026-11-22).
- `os/test_bluetooth_firmware.py`: only the pinned firmware files are admitted.

## Device steps

Preconditions: the Q2 runs a `q2nd` built from this branch (app deploy; no
kernel or image change is needed for steps 0–1), the player's Bluetooth is off
(`[bluetooth] enabled = false`, the default), and Wi-Fi is on. Each command
below runs on the Q2; from the host, for example:

```sh
tests/perf/device_watch.py --name bt-step1 -- sh -c \
  "echo 'q2nd --bt-probe version --firmware /nonexistent' | tests/perf/device_probe.py --stream --timeout 120"
```

Steps 1–5 are not read-only: they drive the controller, and step 3 writes
`/state/bluetooth/devices`. Output is counts, classes and states; no address
or name is printed. Each step stops the controller when it ends.

Firmware for steps 1–5, until an image carries it: copy the two stock files from
the host to `/state/bluetooth/firmware/` on the Q2 with the rig's SSH helper.
Recorded in `9bb5b94a0` (2026-11-21).

0. Wiring evidence, read-only:

   ```sh
   ls -l /dev/ttyS0; cat /proc/tty/driver/serial | head -3
   grep -E 'PD2[3-6]|uart0' /sys/kernel/debug/pinctrl/*/pinmux-pins 2>/dev/null
   ls /sys/devices/platform/bt-power 2>/dev/null; ls /lib/firmware/rtlbt /state/bluetooth/firmware 2>/dev/null
   ```

1. Controller version, from the ROM without firmware, then with it:

   ```sh
   q2nd --bt-probe version --firmware /nonexistent
   q2nd --bt-probe version --firmware /state/bluetooth/firmware
   ```

   Pass: `phase=Ready`, `lmp_subversion`, `rom_version=0` or `2`. "does not
   answer" with the pins unmuxed in step 0 means patch 0181 is needed.
2. Scan: `q2nd --bt-probe scan --firmware /state/bluetooth/firmware`.
   Pass: `found` ≥ 1 with the headphones in pairing mode.
3. Pair: `q2nd --bt-probe pair --firmware /state/bluetooth/firmware`
   (`--nearest` if other audio devices are discoverable; it needs the
   headphones at −60 dBm or stronger and 10 dB above the next).
   Pass: `stream_open=true`. `--any` considers devices of any class: those
   that take audio by class or inquiry response, or when there are none,
   every device found, under the same one-device or `--nearest` rule; a
   device not of the audio class is paired only after the SDP check
   (`chosen … sdp_check=true`, `sink_checks asked=1 sink=1`).
4. Play: `q2nd --bt-probe play 20 --firmware /state/bluetooth/firmware`:
   a 440 Hz tone at −20 dBFS for 20 s. Pass: heard, `dropped_packets=0`.
5. Reconnect: switch the headphones off, run
   `q2nd --bt-probe wait 60 --firmware /state/bluetooth/firmware`, switch them
   on. Pass: `stream_open=true`. Then the player: Settings › Connections ›
   Bluetooth › On, play music, switch the headphones off (expect pause), on
   (expect reconnect; Play resumes).
6. Power: battery ABBA of playback to the jack against playback to the
   headphones with the screen off, by the method in the power memory notes;
   then idle with Bluetooth on and connected against Bluetooth off.
7. Volume and EQ, in the player, with headphones that take absolute volume:
   the log line "Bluetooth: the headphones take absolute volume" after
   connecting; the wheel changes the headphones' loudness over their whole
   range; their own buttons move the on-screen volume; Equalizer shows the
   device on the Output row and its matched profile; after a disconnect the
   jack plays at its own volume and profile; reconnecting restores the
   device's volume.

## Device results — 2026-11-21, V0.126

Run by the coordinator on the Q2 with the V0.126 kernel and image.

| Step | Result |
| --- | --- |
| 0 | PD23–PD26 are claimed by `10030000.serial` (`uart0-data`, `uart0-hwflow`): patch 0179 is installed. `/dev/ttyS0` exists. `/sys/devices/platform/bt-power/state` reads `disabled`. `/lib/firmware/rtlbt` has both files. `/proc/tty/driver/serial` is absent |
| 1, ROM | pass: bring-up 321 ms, HCI 0x0b revision 0x000f, LMP 0x0b subversion 0x8723, manufacturer 0x005d |
| 1, firmware | pass: bring-up 1,505 ms, ROM version 2 (the 44,469-byte patch); 1,500,000 baud without flow control works for the whole download. Afterwards HCI revision 0xd5c8, LMP subversion 0x1143 |
| 2 | **fail**: `scan_ms=40014 found=0`. 40 s is the probe's own limit: the controller never reported the 10.24 s inquiry complete |

Device-verified by this: UART0 routing of TXD/RXD, H5 at 115200 8E1, the ROM
version, the rate change to 1.5 M, the patch download, and the `bt-en` hold.

### Step 2, second run (build `32a9e0bd`)

`scan_ms=13813 found=0`; `hci events=193 inquiry_results=0 inquiry_completes=0
scan_timeouts=1 command_errors=[] h5 sleep=0 wakeup=0 woken=0
retransmissions=0 rejected=0`.

- **The H5 sleep diagnosis was wrong for this controller**: it sent no Sleep
  or Wakeup. The host's handling of those messages stays (it is the
  protocol), but it did not explain the failure.
- The inquiry was accepted (no command error, no timeout), produced no result
  and no completion in 14 s, and the transport was clean. A 10.24 s inquiry
  that does not end by itself is not an empty room: the controller's
  baseband did not run the inquiry to its end.
- The version line of that run (`0x000f`/`0x8723`) is the read *before* the
  download, as designed; it does not show whether the patch took. The probe
  now labels it `at_start` and reads the version again after the download
  (`after_patch`).
- The Wi-Fi driver's coexistence page at the time: two antennas, non-shared;
  `B_FW 0x00000000`, `BT_Rpt 0xff`; scoreboard BT→WL `0x8b` (BT on, bit 7 =
  BT in low-power state), WL→BT `0x8006`; BT counters all 0. So the Wi-Fi
  firmware saw the controller as on, and received no report from it.

Open, in the order the next run separates them:

1. The Bluetooth clock does not advance (a missing clock or a baseband held
   in a low-power state): `diag` reads the clock twice.
2. The baseband runs but the inquiry needs something from the Wi-Fi side
   (radio calibration or antenna grant): the same `diag` with Wi-Fi off.
3. The patch or its configuration is at fault: `diag` on the ROM alone.

No Realtek vendor command that "enables the radio" after the download was
found: the stock `rtk_hciattach` syncs, downloads and changes rate, and the
stock `hci_uart_h5` module's extra code (`rtk_coex.c`) reports Bluetooth
profile activity to the Wi-Fi driver over a UDP socket. That exchange tunes
coexistence; whether the radio depends on it is what run 2 answers.

### Next commands

`q2nd --bt-probe diag` prints `after_patch` (the version after the download),
`clock ticks=… expected=…`, `inquiry length=2 complete=… results=…` (a 2.56 s
inquiry), the event codes seen and a timeline of the last 40 events.

```sh
q2nd --bt-probe diag --firmware /lib/firmware/rtlbt     # A: patched, Wi-Fi on
q2nd --bt-probe diag --firmware /nonexistent            # B: ROM only, Wi-Fi on
```

C, Wi-Fi off. This is safe for the controller: the probe writes `enabled` to
`/sys/devices/platform/bt-power/state` before it opens the port, which holds
BT_DIS_N whatever MSC1 does. Switching Wi-Fi off first lets BT_DIS_N drop,
which only resets the controller to its ROM; the probe then downloads the
patch again. The USB SSH link does not use Wi-Fi. Switch Wi-Fi off in
Settings (or with the network helper's `down`), check
`cat /sys/devices/platform/bt-power/state` reads `disabled`, then:

```sh
q2nd --bt-probe diag --firmware /lib/firmware/rtlbt     # C: patched, Wi-Fi off
```

| A | B | C | Reading |
| --- | --- | --- | --- |
| clock ticks 0 | | | the baseband clock is stopped; compare B and C for whether the patch or Wi-Fi stops it |
| ticks ≈ expected, `complete=false` | `complete=true` | | the patch or its configuration blocks the inquiry |
| ticks ≈ expected, `complete=false` | `complete=false` | `complete=true` | the Wi-Fi side blocks Bluetooth's radio: coexistence work in the Wi-Fi driver |
| `complete=true results=0` | | | the baseband runs and hears nothing: antenna path |
| `complete=true results>0` | | | the 10.24 s scan differs from the 2.56 s one: rerun `scan` |

### Diag runs and a working scan (build `768bd11b`)

Logs: `build/hardware/bt-diag-{A,B,host}.txt` in the main checkout.

- A and B both ran on a controller that had kept its patch (`patch_kept=true`,
  `at_start` revision 0xd5c8, subversion 0x1143), so B was not a ROM-only run.
  Clock: 3,213 and 3,214 ticks in 1,004 ms (3,212 expected). The 2.56 s
  inquiry completed at 2,570 ms with 0 results. Event codes: one vendor event
  (0xff), 15 command completes, one command status, one inquiry complete.
- With the owner's PC adapter made discoverable: `scan` took 10,063 ms,
  `found=1 named=1`, `inquiry_results=6 inquiry_completes=1`, no timeout.

Device-verified by this: the baseband clock runs, inquiries complete, the
radio receives, and inquiry results and the name arrive with Wi-Fi on. The
`found=0` of the earlier runs was a room with nothing discoverable (phones
and computers answer inquiries only while their Bluetooth settings page is
open).

**Not explained:** the two earlier scans whose inquiry was never reported
complete (40 s on `9bb5b94a` with a kept patch; 14 s on `32a9e0bd` right
after a download). It has not recurred, the transport was clean both times,
and nothing in the host between those builds and `768bd11b` touches the
inquiry (the later build adds a version read after the reset and the
diagnostics). Run C (Wi-Fi off) and a ROM-only run were not done. The host
now treats it as a fault it can meet again: a scan whose inquiry is not
reported complete in 14 s cancels it and runs one more, then ends
(`scan_timeouts` counts each); the listener sees "Search for Headphones"
again rather than a search that never ends. To look for it on purpose, scan
right after a download: switch Wi-Fi off and on (BT_DIS_N drops, the
controller returns to its ROM), then `q2nd --bt-probe scan`, and read
`patch_kept=false`, `after_patch`, `inquiry_completes` and `scan_timeouts`.

The search in Settings and `--bt-probe scan` are the same host command: a
general inquiry of 10.24 s whose results appear as they arrive, a second
10.24 s inquiry when the first found nothing, then name requests for devices
whose response carried no name. An inquiry not reported complete by 14 s is
cancelled; with nothing found a second one runs, with devices found the scan
goes on to the name requests with them.

### Pairing with a computer, V0.128 (2026-11-21)

`q2nd --bt-probe pair --nearest --firmware /lib/firmware/rtlbt` against the
owner's PC (BlueZ and PipeWire, Audio Sink 0x110b offered, discoverable and
pairable with a NoInputNoOutput agent). Recorded in `51c3fc487` (2026-11-22).

- `scan_ms=27818 found=1 audio=0 named=1`, `class=0x7c0104 count=1`, then
  `pair refused: 0 audio devices`. The PC's class is major Computer, minor
  Desktop: the probe considered audio-class devices only. `--any`, the
  `ConnectSink` SDP check and the Settings list above are the change for it.
- `patch_kept=false` (a fresh download), `hci events=199 inquiry_results=4
  inquiry_completes=0 command_timeouts=0 scan_timeouts=2 command_errors=[]`,
  H5 `retransmissions=0 rejected=0`.

**The inquiry that does not complete.** Four runs now:

| Build | Patch | Inquiry | Results | Completed |
| --- | --- | --- | --- | --- |
| `9bb5b94a` | kept | 10.24 s | 0 | no, 40 s |
| `32a9e0bd` | fresh download | 10.24 s | 0 | no, 14 s |
| `768bd11b` (diag A, diag host, scan) | kept | 2.56 s, 2.56 s, 10.24 s | 0, 0, 6 | yes: 2,570 ms, 2,570 ms, about 10.06 s |
| V0.128 pair | fresh download | 10.24 s twice | 4 | no, 14 s each |

Both runs right after a download stalled; one of four on a kept patch did
(on an older build).

- **Not lost in transport**: H5 rejected and retransmitted nothing in either
  stalled run, so every reliable packet the controller sent arrived once.
- **Not mis-parsed**: the host counts every HCI event by code before handling
  it (`inquiry_completes` counts code 0x01 whatever the scan state). Until
  this change it dropped, uncounted, an event whose length byte disagreed with
  its H5 packet and any packet that was neither an event nor ACL data; those
  are now counted as `unparsed` (printed on the `hci` line). A 0x01 lost that
  way would show there.
- **Not the extended inquiry mode**: mode 2 changes the result event (0x2f)
  and not the completion; with mode 2 set, the 2.56 s diag inquiries ended
  with 0x01 at 2,570 ms.
- **The controller considered the inquiry still running at 14 s**: the
  Inquiry Cancel sent then was answered with status 0 both times
  (`command_errors=[]`). The specification has a controller answer Command
  Disallowed (0x0c) when no inquiry is in progress; whether this controller
  follows that rule is not verified.

So the controller ran the inquiry past its 10.24 s length; the host can do no
more than stop waiting. Not established: why, and why after a fresh download.
One candidate, unverified: coexistence with Wi-Fi giving Bluetooth too little
air time while the new patch and the Wi-Fi firmware settle (the stock stack's
coexistence module reports Bluetooth activity to the Wi-Fi driver; this host
sends no such report).

The host change: an inquiry not reported complete by 14 s is cancelled, and
when it has found devices the scan goes on with them (names, then
`ScanFinished`) instead of a second 14 s inquiry. The V0.128 run would have
ended at about 14 s with the PC listed. `--bt-probe scan` and `pair` print the
timeline of inquiry events (`t=… event=… opcode=… status=…`: codes, times and
statuses only) when a scan timed out, and `diag` now waits three times the
inquiry length, so an overrunning inquiry is timed:

```sh
q2nd --bt-probe diag --inquiry-length 8 --firmware /lib/firmware/rtlbt   # after a fresh download: complete=… after_ms=…
```

## Dropouts (2026-11-22, V0.133)

The device owner: "I'm getting bt audio dropouts. Short, just a blip really" (Sony
WH-1000XM5, LDAC), then "These blips are happening on AAC with my bose
qc45's". AAC is a quarter of the 1.5 Mbaud UART, so the average rate is not
the cause; bursts and stalls are. His problem report carried no Bluetooth
state; its flight recorder showed only the presses that filed it. Nothing
below has run on a Q2.

### What the code did (V0.133)

- **H5:** window 4, and an unacknowledged packet was sent again after 250 ms.
  There is no negative acknowledgement, so one lost frame (a controller
  receive overrun: no flow control) stopped all audio for 250 ms and then
  resent the window.
- **Pacing:** by the clock, no lead over real time, at most 4 media packets
  queued in the host. After a stall of up to 150 ms the gap was sent at once;
  after a longer one the schedule restarted at the present, so the gap was
  never sent and the headphones' buffer stayed that much lower for the rest
  of the stream. Each later stall then had less buffer to drain.
- **Drops:** a 12-packet cap in the host queue; the pacer stops at 4, so it
  was never reached.
- **UART writes:** non-blocking, waiting up to 2 s for room; the thread reads
  nothing while a write waits. Not timed.
- **Thread:** `bluetooth`, a plain thread at nice 0 (SCHED_OTHER), like the UI
  and decode threads. It is the stream's clock: while it does not run, nothing
  is sent. No thread in `q2nd` raised its priority (nice -10 since; see
  [Thread priority](#thread-priority-nice--10)).
- **Controller buffers:** credits from Read Buffer Size (8 in the fake; the
  Q2's controller value is not recorded) and Number of Completed Packets. A
  stall waiting for them was not measured.

### UART budget

Bytes on the line per media packet: the codec payload, RTP 12 (not aptX),
the LDAC/SBC count byte, ACL 4, L2CAP 4, H5 4, two delimiters, and 2/256 for
SLIP escapes; 11 bits a byte (8E1). 1.5 Mbaud carries 136.4 kB/s, 3 Mbaud
272.7 kB/s. Host-to-controller only; the other direction carries
acknowledgements and Number of Completed Packets.

| Codec, rate | Packets/s | Bytes/packet | kB/s | 1.5 Mbaud | 3 Mbaud |
| --- | --- | --- | --- | --- | --- |
| SBC bitpool 53, MTU 895 | 49 | 867 | 42.7 | 31 % | 16 % |
| AAC 256 kbit/s, MTU 672 (2 fragments per access unit) | 86 | 402 | 34.6 | 25 % | 13 % |
| aptX 352.8 kbit/s | 100 | 458 | 45.8 | 34 % | 17 % |
| aptX HD 529.2 kbit/s | 100 | 691 | 69.3 | 51 % | 25 % |
| LDAC 303 (MQ) | 57 | 692 | 39.8 | 29 % | 15 % |
| LDAC 363 (Q1) | 69 | 692 | 47.7 | 35 % | 17 % |
| LDAC 452 (Q0) | 86 | 692 | 59.6 | 44 % | 22 % |
| LDAC 606 (SQ, the default) | 115 | 692 | 79.5 | 58 % | 29 % |
| LDAC 909 (HQ) | 172 | 692 | 119.3 | 87 % | 44 % |
| LDAC 660 at 48 kHz | 125 | 692 | 86.5 | 63 % | 32 % |
| LDAC 990 at 48 kHz | 188 | 692 | 129.8 | 95 % | 48 % |

`uart_mode = "auto"` runs 3 Mbaud: LDAC 990 at 48 kHz uses about 48 % of
the line there, against 95 % at 1.5 Mbaud. At the measured line rate (2.70
Mbaud, interrupt gaps included; [result](#device-result-3-mbaud-works-2026-11-22-kernel-pm-q2-perf-v134-with-0192-ram-boot-over-v0135)) it is
about 53 %.

The headroom sets how fast a stall is caught up: at LDAC 606 on 1.5 Mbaud,
a 100 ms gap takes about 140 ms of full-rate sending; at 909, about 670 ms.

### Changes (host-tested)

- **H5** (`h5.rs`): resend after 100 ms once the controller runs at the fast
  rate (250 ms during bring-up). Counters: resend timeouts, damaged frames
  (checksum, length, SLIP) and out-of-sequence packets, apart.
- **Pacing** (`bluetooth/pacing.rs` `Pacer`, `bluetooth/media.rs`): by the
  clock with a 60 ms lead, built at 5 % of stream time so it does not arrive
  as a burst; a stall is caught up in full as fast as the controller takes
  it, up to 300 ms behind; beyond that the headphones have run dry and the
  schedule restarts (counted). Packets more than 40 ms late are counted;
  stalls of 100 ms or more are marked with their length.
- **LDAC adaptive bit rate** (`pacing.rs` `Abr`, `q2-ldac` `set_level`):
  libldac's own steps HQ 909, SQ 606, Q0 452, Q1 363, MQ 303 kbit/s (44.1 kHz)
  through `ldacBT_set_eqmid`/`ldacBT_alter_eqmid_priority`, applied from the
  next frame; the vendored `abr/` library is not used. The input is the queue
  between the encoder and the air in packets (host queues, H5, controller
  buffers, plus the schedule's lateness): 8 or more and growing for 200 ms is
  one step down; 14 or more for 200 ms is MQ at once; 3 or fewer for 5 s is
  one step up, never above `ldac_quality`. A step up undone within 30 s
  doubles the wait (up to 60 s); two quiet minutes reset it. One H5 resend
  (100 ms) is at most one step. `[bluetooth] ldac_adaptive` (default true);
  `ldac_quality` is the ceiling. Configs saved by earlier releases carry
  `ldac_quality = "standard"`, which stays the ceiling.
- **Coexistence report** (`host.rs`): the controller is sent Realtek's
  profile report (vendor command 0xfc19, as the stock `rtk_coex.c` does)
  when the link comes up (A2DP on the link), when streaming starts and stops,
  and at disconnect. The controller's firmware passes it to the Wi-Fi
  firmware, whose coexistence tables differ with A2DP known (TDMA 113, 8 ms
  Wi-Fi slots) from without (TDMA 103/104, 32–48 ms Wi-Fi slots;
  `halbtccommon.c` in the Wi-Fi driver). Answered on the fake; not verified on
  the controller.
- **UART** (`bluetooth/mod.rs`, `[bluetooth] uart_mode`): `auto` (the
  default, also for saved configs) runs 3 Mbaud with RTS/CTS (rate code
  `0x04928001`, the flow bit in the configuration's UART entry, as the stock
  configuration has) where the kernel takes the rate, device-verified on
  2026-11-22 with patch 0192 ([result](#device-result-3-mbaud-works-2026-11-22-kernel-pm-q2-perf-v134-with-0192-ram-boot-over-v0135)). A failed bring-up is tried
  at 1.5 Mbaud with RTS/CTS, then without; a kernel that refuses 3 Mbaud
  starts at 1.5 Mbaud with RTS/CTS. `manual` uses `baud` and `flow_control`,
  then 1.5 Mbaud without flow control ([UART fallback](#uart-fallback)).
  History: `auto` ran 3 Mbaud first; from the 0191 result to the 0192 result
  it ran 1.5 Mbaud with RTS/CTS.
- **Link quality:** while streaming, Read RSSI and Get Link Quality every
  2 s; Read Transmit Power Level and Read AFH Channel Map every 10 s. Kept
  out of the event timeline. The headphones' Device ID (vendor source,
  vendor, product, version) is the one SDP read of the
  SDP client ([Stack](#stack)), saved per device; telemetry reports
  the saved value, the same one the [EQ match](#headphone-eq-per-output) uses.

### Simulation (`bluetooth/sim_tests.rs`)

The host, pacer, encoder and fake controller step by 1 ms. The UART carries
136 bytes a millisecond after bring-up; the radio sends one ACL packet per
3.75 ms (2-DH5 and its return slot) and can stall or slow; writes can be lost;
the thread can be held off the CPU. The headphones are modelled as a buffer
that starts at 100 ms and is a gap when it empties (real sinks keep 150–250 ms).

| Scenario | Gaps | Lowest buffer |
| --- | --- | --- |
| LDAC 606, clean, 6 s | 0 | 96 ms |
| LDAC 606, a lost media write every 1.5 s (9 resends) | 0 | 33 ms (118 ms silences; the ABR ends at MQ) |
| LDAC 606, 30 ms of no air every 100 ms for 5 s | 0 | 96 ms |
| LDAC, air 3× slower for 8 s, fixed 606 (control) | > 0 | — |
| LDAC, the same, adaptive, 48 s | 0 | 47 ms (back at 606 at the end) |
| AAC 256: 4 lost writes, 20 air stalls of 30 ms, 4 × 80 ms with the thread off the CPU | 0 | 47 ms |

With V0.133's 250 ms resend and no lead, one lost frame alone is a 250 ms
silence against a buffer the model starts at 100 ms.

### Telemetry

Every UX report while Bluetooth is on carries a `bluetooth` object and the
snapshot carries the output route ([telemetry.md](telemetry.md)): state,
codec, rate, bit rate, LDAC level, MTU, SBC bitpool, UART rate and flow
control, UART fallbacks, the headphones' class, name and Device ID, RSSI, link quality,
transmit power, AFH channels, controller buffers in use, H5 and UART receive
error counts, credit stalls, late packets, schedule restarts, the queue's
depth, LDAC changes, underruns, late wakes of the thread (20 ms or more past
its timeout), the longest UART write and the slowest encode. Flight-recorder
marks: `bt-stall` (lateness, restarts, credit stalls, late wakes, slow
writes), `bt-transport`, `bt-rate`, `bt-state`, `wifi-event` (supplicant
scan, association, connect, disconnect) and `wifi-scan` (the player's scan
requests and why). `python3 tools/ux_triage.py --boot latest` prints them.
**The receiver must run this branch's `tools/diagnostic_receiver.py` before
the app is installed**: the old one refuses the new fields.

### Thread priority (nice -10)

The device owner, 2026-11-22: "Please do increase the bt thread's priority." The
`bluetooth` thread sets its own nice value to -10 when it starts
(`bluetooth::raise_priority`, `platform::system::set_thread_nice`: one
`setpriority(PRIO_PROCESS, 0, -10)`; Linux keeps nice per thread, and `who`
0 is the calling thread). The UI, decode and other threads stay at nice 0.
The outcome is logged once per process: `Bluetooth: thread at nice -10`, or
a warning with the error, after which the thread runs at nice 0 as before.

- **Permission:** `os/install/root-init` starts
  `/sbin/q2-installed-services`, which runs `q2nd` with no change of user, so
  it runs as root and has CAP_SYS_NICE. On a host without the privilege the
  call fails with EACCES and nothing changes; the test
  (`platform::system::tests::thread_nice_changes_only_the_calling_thread_and_a_refused_raise_changes_nothing`)
  accepts either outcome and checks that only the calling thread changes.
- **Why nice and not SCHED_FIFO/RR:** the thread encodes as well as sends
  (LDAC at 909 kbit/s is its largest CPU cost), and the decode thread feeds
  it. A realtime thread runs until it blocks, ahead of every SCHED_OTHER
  thread; a busy loop or a long encode would hold off decode, the UI and the
  network on the Q2's two logical CPUs for up to the kernel's realtime
  throttle (95 % of each second). Nice -10 gives it about nine times the
  weight of a nice-0 thread under the kernel's fair scheduler (EEVDF), so it
  wins the CPU when it wakes in most cases, and the other threads still run.
  SCHED_FIFO is the next step if the late wakes stay high.
- **Threads it creates** inherit nice -10. On the Q2 it creates none (the
  fake controller's thread in tests and the simulator does).
- **Measuring it:** the late-wake telemetry is unchanged: `wake_late` and
  `wake_late_max_ms` in each report's `bluetooth` object, and `bt-stall` tag
  3 marks of 50 ms or more. Compare them with a V0.133-era report under the
  same load. On the Q2, `cat /proc/$(pidof q2nd)/task/*/stat` shows the
  `bluetooth` thread with nice -10 (field 19). Not yet run on a Q2.

### Kernel: 3 Mbaud (patch 0191, profile `pm-q2-perf-v134`)

[Patch 0191](../kernel/patches/0191-serial-8250_ingenic-UMR-oversampling-for-rates-above-uartclk-16.patch)
gives the `ingenic,x2000-uart` ports get/set-divisor hooks: a rate the 16×
divisor reaches within 2 % is programmed as before (and UMR set to 16); any
other uses the UART's M register (oversampling 6–16) with the divisor, so
24 MHz gives 2, 3 and 4 Mbaud exactly (M 12, 8, 6); 921,600 comes within
0.16 % instead of 18.6 %. UPF_MAGIC_MULTIPLIER raises the core's ceiling
and a set_termios hook keeps rates the hooks cannot reach clamped as before.
It applies with no fuzz to the v127 tree and compiles without warnings with
the V0.127 build's compiler (one object, no link). Unverified: the M range,
that UMR/UACR take effect with DLAB set, and UACR = 0. `kernel/q2_perf_v134.py`
is v127 plus patch 0187 (the codec's HP Full Scale and HP Class H controls)
and 0191, in that order; it is the kernel recipe `pm-q2-perf-v134`
(`python3 tools/q2.py build-test` with `"mode": "kernel", "recipe":
"pm-q2-perf-v134"`), configuration unchanged from v127's.

**Flow control:** with CRTSCTS the 8250 core enables modem-status interrupts,
and the Ingenic driver then sets MCR MDCE|FCM (hardware RTS/CTS). PD25/PD26
are muxed as `uart0-hwflow` (patch 0181). Flow control works on the board:
device-verified on 2026-11-22 at 1.5 Mbaud (below).

#### Device result (2026-11-22, kernel `pm-q2-perf-v134`)

The Q2 RAM-booted on kernel recipe `pm-q2-perf-v134` (patch 0191), with
`q2nd --bt-probe version`:

| Probe (`q2nd --bt-probe version`) | Result |
| --- | --- |
| 1.5 Mbaud, no flow control | Ready |
| 1.5 Mbaud, `--flow` (RTS/CTS) | Ready in 2.0 s, 14 HCI events, 0 retransmissions: flow control works on this board |
| 3 Mbaud, with or without flow | "The Bluetooth controller does not answer" |
| 2 Mbaud, with flow | same failure |

termios reads back 3 M, 4 M and 2 M, so the kernel accepts the rates, but the
line does not run at the rate the controller is set to. The likely cause is
that UMR does not take effect on the X2000 (not measured; a scope or a
loopback at the TX pin would show the line rate). Not known: whether the 2
Mbaud run started from a controller left at 3 Mbaud by the run before it (a
controller left at a rate the UART does not produce stays unreachable until
BT_DIS_N drops; see the fallback below). Consequence then: `uart_mode = "auto"`
became 1.5 Mbaud with RTS/CTS. Superseded: with patch 0192 and the controller
reset between probes, 3 Mbaud works ([result](#device-result-3-mbaud-works-2026-11-22-kernel-pm-q2-perf-v134-with-0192-ram-boot-over-v0135)), and `auto` runs it
again.

#### UART fallback

`uart_mode = "auto"` tries three UART settings in order (`AUTO_UART` in
`bluetooth/mod.rs`):

1. 3 Mbaud with RTS/CTS (rate code `0x04928001`, the flow bit set in the
   configuration's UART entry), the stock firmware's setting;
2. 1.5 Mbaud with RTS/CTS (`0x04928002`);
3. 1.5 Mbaud without flow control.

A kernel that does not take 3 Mbaud (termios reads another rate back,
`Uart::accepts`) starts at step 2: one info line in the log, no fallback
counted, no error shown. `manual` tries `baud` and `flow_control`, then
1.5 Mbaud without flow control; a configured rate the kernel refuses goes
to that step without a first attempt and counts as a fallback.

A bring-up that fails before Ready at its setting (the H5 sync gets no
answer, a start-up command or a firmware fragment goes unanswered or is
refused, the controller refuses the rate, or the port refuses the setting)
is tried at the next. The thread logs `Bluetooth: bring-up at B baud, flow
control F failed (…); trying B2 baud, flow control F2`, keeps the message as
the session's last error (Bluetooth Details), and counts it:
`uart_fallbacks` in each report's `bluetooth` object (`ux_triage.py` prints
`fallbacks=`). The page shows `Starting…` through every attempt. The
setting that reaches Ready is used at once by later starts of the same
player process; a start in which every setting fails shows the failure with
Try Again, which starts again at the first setting. `--bt-probe` with
`--baud` or `--flow` does not fall back, so a probe tests the setting it
names.

**Resetting the controller between attempts.** The thread drops the power
hold for 100 ms. That resets the controller to its ROM at 115200 only when
nothing else holds BT_DIS_N, that is with the Wi-Fi slot powered down: in the
mainline device tree BT_DIS_N (PD20) is the enable GPIO of the fixed
regulator that supplies WL_DIS_N, MSC1's `vmmc-supply`, and the hold is a
second fixed regulator (`bt-en`) on the same GPIO; the kernel keeps a
shared enable GPIO high while either regulator is on
([patch 0181](../kernel/patches/0181-MIPS-Q2-mux-UART0-for-Bluetooth-and-add-a-Bluetooth-enable-hold.patch),
[kernel/rtl8733bs](../kernel/rtl8733bs/README.md)). With Wi-Fi on, dropping
the hold changes nothing. An HCI Reset does not help either: the controller
keeps its patch and rate through it (our start-up commands send one at the
fast rate, and so does Linux's HCI core after `btrtl` sets the rate). No
Realtek vendor command that returns a UART controller to its ROM rate is
known here. So the next attempt's H5 sync tries, in turn, 115200 (a reset
controller), its own rate (a controller that kept its patch at it) and the
failed attempt's setting (`Settings::previous` in `crates/bt/src/host.rs`).
A controller that answers at the failed setting is taken as patched, sent the
vendor rate command (0xfc17) with the new rate at the old setting, and the
host retunes once the acknowledgement has arrived, as in a normal bring-up;
the start-up commands follow, without a download. The vendor command leaves
the controller's flow-control setting from its patch configuration as it
was: a controller moved from 3 Mbaud with RTS/CTS to 1.5 Mbaud without keeps
RTS/CTS on its side.

What this does not cover, with Wi-Fi on:

- a controller that took a rate the UART does not produce (the failure mode
  of a kernel that accepts 3 M in termios but does not run the line at it):
  it cannot be reached at any setting, every attempt fails, and Bluetooth
  shows the failure; Wi-Fi Off, then Try Again, resets it through the hold;
- a controller left in its ROM at the failed setting (a download that failed
  part-way) is taken as patched and comes up without its patch.

Host-tested with the fake controller (`bluetooth/tests.rs`): auto comes up
at 3 Mbaud with RTS/CTS; a line that does not run at 3 Mbaud falls back to
1.5 Mbaud with RTS/CTS and the next start uses it; broken RTS/CTS falls back
twice, to 1.5 Mbaud without flow control; a kernel without 3 Mbaud starts at
1.5 Mbaud with RTS/CTS with no fallback counted; a controller kept powered
across attempts (the Wi-Fi case) that failed at 3 Mbaud after its patch is
found there and moved to 1.5 Mbaud (the same test fails with the previous
setting left out of the sync); every setting failing ends in Failed; a probe
naming a rate does not fall back. `crates/bt/src/stack_tests.rs` checks the
sync order and the move at the byte level. Not run on a Q2.

### Kernel: 3 Mbaud (patch 0192)

The stock V1.32 kernel programs the same values as patch 0191 for 3 Mbaud
(divisor 1, UMR 8, UACR 0, from the same 24 MHz EXCLK) but writes UMR and
UACR after LCR clears DLAB; 0191 wrote them with DLAB set. Evidence and the
full stock sequence: [stock-firmware.md](stock-firmware.md#bluetooth-uart-rate-in-the-stock-kernel).
[Patch 0192](../kernel/patches/0192-serial-8250_ingenic-write-UMR-and-UACR-after-DLAB-clears-as-the-stock-kernel-does.patch)
applies on 0191 and is part of recipe `pm-q2-perf-v134` from this commit:

- UMR and UACR after LCR without DLAB, then the core's LCR and FCR as
  before: stock's order.
- `8250_ingenic.umr_seq` (`/sys/module/8250_ingenic/parameters/umr_seq`,
  0644): 0 stock's order (default); 1 the same with FCR.UME cleared while
  M changes, as the X1000 manual's configuration sequence; 2 0191's order.
  Read at each rate change, so one boot tries all three.
- A rate with M other than 16, or a register that does not read back as
  written, logs `ttyS0: 3000000 baud: divisor 1, UMR 8 (reads N), UACR 0
  (reads N), umr_seq S`.

Build (host, 2026-11-22), all under `build/kernel-v134/`: `dlab-01`
(source, configuration unchanged, `.config` SHA-256 `4bf1eac1…`; kernel
receipt `dlab-01/kernel-build`, vmlinux `ff4468b5…`, closure `507be6d2…`;
no compiler warnings); `dlab-01-rtl` (8733bs.ko `a3bf617a…`); RAM plan
`dlab-01-ram/plan.json` over the V0.135 image
(`build/worktrees/q2-v129/build/q2/20261122-192632-build-test-3247079a/image`),
binding only 8733bs.ko (the other 48 modules equal V0.135's). The built
`8250_ingenic.o` calls `serial8250_do_set_divisor`, writes LCR, then UMR
(0x24) and UACR (0x28), reads both back and calls `_printk_deferred`
(disassembly). Boot: `python3 tests/perf/perf_kernel_ram.py boot --plan
build/kernel-v134/dlab-01-ram/plan.json --out build/kernel-v134/dlab-01-ram`;
`return` with the same arguments goes back to the installed kernel. Not run
on a Q2.

**Device test** (after the RAM boot; through `tests/perf/device_probe.py`):

1. Wi-Fi off in Settings (or `timeout -k 3 45 /usr/libexec/q2-network/wifi
   down`), the player's Bluetooth off; `cat
   /sys/devices/platform/bt-power/state` reads `disabled`. With Wi-Fi off,
   BT_DIS_N follows only the probe's hold, so each probe below starts from
   a controller reset to its ROM at 115200 (no rate left over from the run
   before).
2. Line rate: `tests/perf/device_probe.py --stream --timeout 120 <
   tests/perf/bt_uart_rate.sh`. It sends 300,000 zero bytes (8N1) at each
   setting with the controller in reset and times them to the last bit
   (close waits for the transmitter). Expected: 1.5 Mbaud 2.0 s; 3 Mbaud
   1.0 s if UMR takes effect, 2.0 s if it does not; 2 Mbaud 1.5 s; 4 Mbaud
   0.75 s. It prints `uartclk` (24000000), each `stty` readback and the
   driver's UMR/UACR line. A line rate a few percent below the set rate is
   interrupt gaps between FIFO refills; 1.5 Mbaud at a 3 Mbaud setting is
   UMR not taking effect.
3. Link: with `umr_seq` 0, `q2nd --bt-probe version --baud 3000000 --flow`.
   Pass: `uart baud=3000000 flow=true`, `phase=Ready`,
   `command_timeouts=0`, `h5 resends=0 bad_frames=0`, `uart frame=0
   overrun=0 parity=0`. If step 2 showed 3 Mbaud only with `umr_seq` 1, run
   this with `echo 1 > /sys/module/8250_ingenic/parameters/umr_seq` first.
   Then `--baud 3000000` without `--flow`, and `--baud 1500000 --flow` as
   the control.
4. Wi-Fi on. `dmesg | grep -E 'ttyS[03]'` for the driver's lines.

### Wi-Fi scans

The device owner (2026-11-22): "We should also very rarely do full wifi scans. Really
only if we can't connect to a known network, and if the user has entered the
wifi connection page" and "make sure background wifi scans don't
unnecessarily wake the radio".

| Source | Before | Now |
| --- | --- | --- |
| Supplicant bgscan, autoscan, P2P | not compiled in (`os/hardware/build-wifi-tools.py`) | same |
| Supplicant's first scan after start (wake from doze) | last channel only (`initial_freq_list`, the helper's `-I` overlay) | same |
| Supplicant's own rescans while not associated (every 5 s after a miss) | yes | same: the known networks cannot be found |
| Full scan after the hinted scan misses (`net/kick.rs`) | once per radio start | same; marked `wifi-scan 1` |
| Band steering's 5 GHz scan while associated on 2.4 GHz (`net/band.rs`) | at most every 30 min, page or not | only while the Wi-Fi page is open (a page scan in the last 2 min); marked `wifi-scan 2` |
| Wi-Fi page | its scans | same, and never for a dozed or switched-off radio; marked `wifi-scan 3` |
| Search policy `RECONNECT` while not associated (`net/search.rs`) | backoff 45 s to 30 min | same; marked `wifi-scan 4` |
| Driver roam scan | on beacon loss only (roaming flag 0x3) | same |
| Dozed radio | interface down, supplicant stopped, no observation (`owner.rs` `monitor_turn`) | same |

No supplicant change is needed for this policy; the rest is the player's.

### Device tests

Preconditions: the receiver from this branch; the app from this branch
(`bt-drop-01`); for 3–5 a kernel built from recipe `pm-q2-perf-v134`.
Commands run on the Q2 through `tests/perf/device_probe.py`.

1. **Telemetry, current kernel.** Settings › Bluetooth on, LDAC headphones,
   music 10 minutes with the screen off, then Report a Problem. Pass:
   `ux_triage.py --boot latest` shows the `bluetooth:` lines with the codec,
   `uart=1500000`, a device name and class, RSSI and link quality, and the
   `bt-state` marks. Read `wake_late`, `h5 resends/bad/out_of_seq`, `uart
   overrun/parity`, `credit stalls` and `restarts`: they say which cause
   remains.
2. **The same with AAC** (the Bose, `[bluetooth] codec = "aac"`), and once
   while a Wi-Fi wake happens (screen on after a dozed minute): look for
   `wifi-event` marks next to `bt-stall` marks.
3. **3 Mbaud accepted (kernel v134).** `dmesg | grep -E 'ttyS0|extra baud'`;
   for r in 115200 1500000 3000000 4000000: `stty -F /dev/ttyS0 $r; stty -F
   /dev/ttyS0 speed` reads $r back; `stty -F /dev/ttyS0 2500000` is refused.
   2026-11-22: 3 M, 4 M and 2 M read back; the link at them failed
   ([result](#device-result-2026-11-22-kernel-pm-q2-perf-v134)).
4. **Flow-control lines.** Wi-Fi off (so BT_DIS_N follows the hold alone),
   `q2nd --bt-probe lines`: `power=off cts=…` then `power=on cts=…`. CTS that
   changes with the controller's power shows its RTS reaches the Q2's CTS;
   CTS asserted in both says the line is not driven by the controller.
5. **Link at 3 Mbaud with RTS/CTS.** 2026-11-22: failed ("The Bluetooth
   controller does not answer"; [result](#device-result-2026-11-22-kernel-pm-q2-perf-v134)),
   while 1.5 Mbaud with `--flow` came up. Patch 0192's test is in
   [Kernel: 3 Mbaud (patch 0192)](#kernel-3-mbaud-patch-0192). Retest after a UMR fix: switch
   Wi-Fi off and on (the controller back to its ROM), then
   `q2nd --bt-probe version --baud 3000000 --flow`. Pass:
   `uart baud=3000000 flow=true`, `phase=Ready`, `command_timeouts=0`,
   `h5 resends=0 bad_frames=0`, `uart frame=0 overrun=0 parity=0`. Then
   `q2nd --bt-probe play 600 --codec ldac --ldac-quality high --ldac-fixed`
   with `--baud 3000000 --flow` and with `--baud 1500000 --flow`: at 3 M
   `restarts=0`, `underruns=0`, H5 counters no higher than at 1.5 M.
   2026-11-22 with patch 0192: the version probe passed at 3 M with RTS/CTS
   ([result](#device-result-3-mbaud-works-2026-11-22-kernel-pm-q2-perf-v134-with-0192-ram-boot-over-v0135)); the play run is not done.
5a. **The default and the fallback (app `bt-3m-01`).** `q2nd --bt-probe
   version` (auto): `uart baud=3000000 flow=true`, `phase=Ready`,
   `fallbacks=0`. Fallback: `uart_mode = "manual"`, `baud = 4000000`,
   `flow_control = true` in `config.toml`, Wi-Fi off, Bluetooth on in
   Settings. 4 Mbaud has not been tried on the link (the line measured
   3.45 Mbaud, gaps included). If it does not come up: Ready, Bluetooth
   Details shows the failed attempt as the last error, the player log has the
   fallback line, and a problem report shows `fallbacks=1` and
   `uart=1500000` without `rts/cts`. If it comes up, the report shows
   `uart=4000000` and `fallbacks=0`, and the fallback stays host-tested.
6. **Coexistence report.** During a stream with Wi-Fi associated on 2.4 GHz:
   `cat /proc/net/rtl8733bs/wlan0/btcoex`. Pass: `Profiles` lists A2DP and the
   TDMA line shows case 113 while Wi-Fi is busy; `command_errors` in the
   probe report has no `0xfc19`.

## Limits

- Bring-up and scan run on the Q2; pairing and audio have not. Interoperability
  with real headphones is untested.
- The SDP check before pairing a device of another class is host-tested only.
- One connected device. No multipoint, no track titles on the headphones'
  display. LDAC, aptX HD, aptX and AAC are host-tested only. Absolute volume,
  the volume keys, the Device ID query and the EQ match are host-tested only.
- System sleep with Bluetooth on is not handled: suspend stalls the thread
  and the link is expected to drop. Not tested.
- Stopping Bluetooth leaves the controller patched and at the fast rate unless
  the kernel offers the power hold; the next start finds it there after 1.5 s.
- Wi-Fi and Bluetooth share the 2.4 GHz radio. The Wi-Fi driver's coexistence
  code is built in; streaming over Wi-Fi while sending SBC is unmeasured.

## Device result: pairing and streaming to a BlueZ sink (2026-11-22, V0.128)

Binary `bt-sink-check-01` (`d411db29…`, from 51c3fc48) run from /tmp on the
Q2, against the owner's Linux PC (BlueZ with PipeWire, class 0x7c0104, Audio
Sink in its inquiry response), the PC discoverable with an auto-accepting
agent:

- `pair --any`: scan 10.1 s, `eir_sink=1`, SDP check found the sink,
  `pair_ms=5111 stream_open=true`, SBC 44.1 kHz bitpool 53, MTU 1021.
- `play 20 --codec sbc` after marking the Q2 trusted on the PC:
  `connect_ms=2169`, `link=playing`, 0 dropped packets, 0 underruns.
- Before the PC trusted the Q2, every reconnect timed out at 20 s; the PC's
  BlueZ policy tried to open A2DP from its side at the same time
  (`a2dp-source profile connect failed ... Transport endpoint is not
  connected`). One AAC reconnect also timed out after trust; the codec runs
  were stopped there because the PC routes audio to AirPlay speakers.
- No PipeWire `bluez` input node was seen on the PC during the runs, so the
  received audio was not captured or checked on the PC side.

Open: real headphones (pairing mode, the reconnect race, codecs other than
SBC); a simultaneous-connect collision from the sink side should be handled
by backing off and accepting the peer's AVDTP session.

## Device result: the device owner's headphones (2026-11-22, V0.128)

- `q2nd --bt-probe pair --nearest`: headphones class 0x240418 found in 14 s,
  paired in 9.9 s, stream open, SBC 44.1 kHz bitpool 53, MTU 672.
- `play 20` per codec: SBC and AAC-LC 192 kbit/s streamed with 0 dropped
  packets and 0 underruns; LDAC and aptX requests fell back to SBC (the
  headphones offer neither). The device owner heard all four tones clean.
- In the player: Settings › Bluetooth On connected to the headphones, music
  played in them, and playback paused when the headphones were switched off
  (the device owner, 2026-11-22).
- The pause on switch-off showed an error (the device owner: "works and pauses but
  shouldn't show an error - just pause"). Cause, from the code (no device log
  was taken): the write in progress failed with ENODEV after the route had
  left, or with EIO after a 3 s stall before the link dropped, and
  `output_failed` showed it; the host's Stream(None) before the disconnect also set "These
  headphones did not accept audio." as Last Problem. Both are fixed and
  host-tested ([audio route](#audio-route)); release build `bt-pause-01`, not
  yet on the Q2.

### Device result: 3 Mbaud works (2026-11-22, kernel pm-q2-perf-v134 with 0192, RAM boot over V0.135)

With Wi-Fi off (controller reset) and the player held:

| Termios rate | Measured line rate (300,000 bytes, `tests/perf/bt_uart_rate.sh`) |
| --- | --- |
| 1,500,000 | 1,421,801 baud (2.11 s) |
| 3,000,000, `umr_seq` 0, 1 and 2 | 2,702,703 baud (1.11 s) each |
| 2,000,000 | 1,863,354 baud (1.61 s) |
| 4,000,000 | 3,448,276 baud (0.87 s) |

The driver read UMR back as written (8, 12, 6) in every order, so UMR also
works in 0191's order: the earlier 3 M failure was not the line rate. App
0.133's `q2nd --bt-probe version --baud 3000000 --flow` then passed: 192 HCI
events, 0 command timeouts, 0 H5 retransmissions, resends, bad frames or
out-of-sequence packets, `uart baud=3000000 flow=true`. The likely cause of the
earlier failures: the controller left at another rate with Wi-Fi on (no
reset between probes), and app 0.131's probe. Not yet run: a 3 M stream test
with headphones. Since app `bt-3m-01`, `uart_mode = "auto"` runs 3 Mbaud
with RTS/CTS and falls back to 1.5 Mbaud with RTS/CTS, then without
([UART fallback](#uart-fallback)).
