# Split installed OS profile

This builder creates a candidate; it never writes a physical device, stages a
card, changes the recovery selector or flashes anything. The application and
network services are supplied by an integration overlay. Absence of its
`/sbin/q2-installed-services` hook produces a diagnostics-only foundation,
explicitly recorded in the artifact manifest.

The stock SPL needs an executable self-decompressing `uzImage.bin`, packaged as
`xImage` with uImage compression **none**. The kernel slot is 6 MiB. A small
rescue initramfs remains in that kernel; the full read-only squashfs, exact
modules, app and Tailscale executables occupy the 75 MiB main rootfs slot.
The installer builds the published production Linux commit and configuration
locked in `os/sources.json`. It verifies the clean checkout before and after
compilation and embeds the generated rescue archive. Startup uses both CPUs.

The DT exposes only read-only rootfs, writable ota and userdata at the known
stock offsets. It does not expose SPL, recovery or identity, nor a writable
master MTD. Patch 0046 maps a read-only raw NAND image across good eraseblocks,
matching stock recovery's default `nandwrite` layout. Writable partitions are
not exposed through this block driver. The mapping is fixed for the mount;
new uncorrectable damage fails rather than silently shifting mounted data.

## Build

Use the clean source from `os/install/prepare.py` and a fresh output directory. Use generated Buildroot and player artifacts and staged Tailscale artifacts.
The default installed and rescue images have USB diagnostics with SSH disabled.

```sh
python3 os/install/prepare.py --dest build/os/install-src
python3 os/install/build.py \
  --kernel-source build/os/install-src \
  --buildroot-target GENERATED_BUILDROOT_OUT/target \
  --app PAIRED_TARGET_BUILD/q2nd --player-source MATCHING_PLAYER_CHECKOUT \
  --wifi-tools build/os/hardware-wifi-tools \
  --tsrsd GENERATED_TSRS_BUILD_DIR \
  --integration-overlay GENERATED_SERVICE_OVERLAY \
  --output build/os/install-candidate
```

`--app` accepts a paired target build from `os/rust-build.py` or
`tests/perf/build-mainline-app.sh`. Keep the neighboring `q2-portal` binary and
both symbol receipts in their original layout. The installer binds the player
to the companion from the same source commit and installs the companion in
`usr/libexec/q2-network/`. Generate the full `--wifi-tools` output with
`os/hardware/build-wifi-tools.py` for the matching SDK, including the private
access-point files needed by phone entry.

The default `--tailscale rs` packages the standalone Rust daemon at
`usr/libexec/q2-network/tsrsd`. The standalone repository and its exact source
revision are listed in [tsrs.json](tsrs.json). Build it from a checkout of that
revision with the same SDK used for the OS:

```sh
python3 TSRS_CHECKOUT/build.py q2 \
  --sdk GENERATED_SDK \
  --toolchain-repo Q2_TOOLCHAIN_CHECKOUT --compiler-work GENERATED_COMPILER_WORK \
  --rust-link Q2_OS_CHECKOUT/os/rust-link.py --out GENERATED_TSRS_BUILD_DIR
```

CI can transfer the resulting directory to the image job as an artifact, keeping
`tsrsd`, `result.json`, `size.json`, and `symbols/receipt.json` together.
For a local build, point `--tsrsd` at any directory with the same pinned artifact
layout. The installer checks the source revision and upstream patch identity,
requires a little-endian MIPS ELF executable, and verifies the hashes before
installing the daemon. The SDK build itself needs no standalone daemon inputs
or access to its repository. Use `--tailscale go --tailscale-dir GENERATED_GO_DIR`
instead to package the Go daemon and CLI from their existing staged directory.

For a local QEMU candidate, pass `--qemu-fixture` instead of
`--wifi-tools` and `--integration-overlay`. This selects the versioned service
overlay, omits hardware radio firmware and modules, leaves SSH disabled and
uses the production kernel and DTB. The local test harness uses QEMU direct
kernel boot and synthetic NAND with the packed root placed at the normal
offset. A generated UBI `user_data` volume permits player startup. The
fixture reports `Q2_SIM_PLAYER_READY` on the serial console after its
supervised player renders the first frame. The package is for local emulator
testing.

The Buildroot `host/` compiler must accompany its `target/`. Output includes
`xImage`, `rootfs.squashfs`, embedded DT, exact modules and `artifact.json`.
The ordinary hardware package validator checks both slot limits and formats.
The manifest records the app, Tailscale, source/config and module provenance.
Before Squashfs packaging, the USB runtime gate requires the installed startup
hook, ownership/status scripts and both target executables. It rejects missing
or non-executable files and host-architecture binaries, and runs the event helper
with an invalid argument through QEMU against the candidate libraries. That
argument exits before device I/O. Refresh builds must apply the same gate; copying
only `q2-usb-auto` without its `q2-usb-auto-events` dependency strands manual
sessions after safe eject. Validate a generated refresh root before packing:

```sh
python3 os/usb_runtime.py GENERATED_ROOT --qemu-user GENERATED_QEMU_MIPSEL
python3 os/test_usb_runtime.py
```

### Personal USB SSH

Add `--authorized-key /absolute/path/to/developer.pub` to build a personal
image. Keep the SSH client private key on the development host. The build
strips the public key comment and generates a fresh Ed25519 Dropbear host
identity shared by the normal root and its rescue for this device. Keep the
resulting output directory and any stock update derived from it private. The
build receipt records the public host fingerprint and the client public key
digest.

To preserve a host identity across rebuilds for the same device, also supply
`--host-key` with its owner-only native Dropbear private key. Provide all key
inputs outside Git worktrees. Select the QEMU user-mode executable with
`--qemu-user` to run the target Dropbear key tool for generation and validation.
The daemon accepts public key authentication over diagnostic USB only. Use a
different host identity for each device.

The standalone packaging control uses generated client keys and packs and
extracts the installed SquashFS and rescue newc archives.

```sh
python3 os/install/test_ssh_provision.py \
  --target build/os/out/target --qemu-user PATH_TO_QEMU_MIPSEL_STATIC
```

### Auditing a narrow refresh

For app/helper-only refreshes, audit the generated baseline and candidate before
the full emulator and final hardware gates:

```sh
python3 os/install/audit_refresh.py \
  --base build/os/install-baseline --candidate build/os/install-next \
  --replace usr/bin/q2nd=EXPECTED_APP_SHA256 \
  --replace sbin/q2-usb-storage=EXPECTED_HELPER_SHA256 \
  > build/perf/refresh-audit.json
python3 os/install/test_audit_refresh.py
```

Declare only files actually changed, with their independently frozen input hashes.
Release metadata is checked separately. The audit verifies artifact manifests,
exact root deltas (including modes, directories and symlinks), app/version
consistency, and unchanged kernel/DTB/rescue/modules/platform. It rejects tree
structure or permission changes; use the full builder for those broader updates.
Inputs must be generated build trees, never mounted owner data. Reports contain
identities/paths only. This audit does not compare the unpacked tree with packed
SquashFS contents, boot an image, or qualify hardware: those remain explicit
packaging/emulator/install gates. Run the USB runtime gate above as well.

## Boot and state contract

`bootstrap` mounts the read-only image through `/dev/mtdskipN` and switches to
`/sbin/q2-installed-init`. If the root image cannot mount, its own BusyBox and USB management gadget remain available. Personal images
also start key-only Dropbear there. SSH requires initialized kernel entropy.
Independent stock key recovery remains available.

`q2-state` locates `userdata` by name and checks the stock offset/geometry before
attaching UBI. The attach helper requires an existing CRC-valid UBI erase-count
header; it refuses erased/unrecognized userdata. It never erases/formats,
creates a volume, or imports stock configuration. The existing `user_data`
UBIFS volume mounts at `/mnt/userdata`; only a fresh `q2-mainline` namespace is
bound to `/state`. USB diagnostics are independent of userdata admission. Personal images use
the provisioned boot identity regardless of userdata health.

The service overlay owns optional SD mounting, canonical host-editable
`/mnt/mmc/Q2Mainline/config.toml`, its validated internal fallback, large
media/cache paths, network startup and supervised immutable-app launch.
`/state/Q2Navidrome` is essential app state. Tailscale state stays internal,
and logs belong in `/run`. Do not copy app rollback generations or unbounded media into userdata.
Do not export a writable card while any local mount/consumer retains it.

## Qualification

Mapping test (the same header is compiled into the driver):

```sh
cc -Wall -Wextra -Werror -I build/os/install-src/drivers/mtd \
  os/install/test_skipbad.c -o build/os/install-review/test-skipbad
build/os/install-review/test-skipbad
```

It compares all 4096 bad-block patterns on a generated 12-block image against
an independently generated skip-bad writer, plus capacity/overflow/scan errors.
The container sanitizer run also passed. Kernel build checks are separate.

`test_boot.py` uses a sanitized `qemu/mkflash.py` full-chip fixture with erased
identity, selector and userdata. It inserts the candidate at the real offsets,
boots the real SPL, and checks protected byte hashes. `--bad-blocks 0,2,7`
marks OOB bad blocks and writes root data around them. `--corrupt-root` checks
the rescue path. Generated `--key` and `--host-public-key` enable actual pinned
USB SSH checks; no physical device address is used. `--userdata-image` accepts
an explicitly generated UBI fixture and requires SSH state checks; `--boots 2`
restarts the emulator using the same NAND and checks a generated persistent
marker. This fixture option is never part of production startup.

Normal and three-bad-block roots reach installed init, with protected partitions
and blank userdata unchanged. The exact V0.11 service-integrated candidate passes
two generated-state boots, no-SD player health and corrupt-root USB SSH. Stock
recovery writes its exact payload bytes; the recovery-written NAND cold boots
to USB SSH, persistent state and live player health. The primary also reports
the first physical V0.11 installation/automatic player boot. Successive updates,
repeated physical boots and broader hardware behavior remain separate checks.
V0.11 subsequently failed its second physical boot after userdata writes;
internal persistence is not qualified. The actual device is Q1GA/E5-71,
128 MiB and one plane, correcting the emulator-derived Q2GA assumption.
See [exact hashes and qualification](../../docs/mainline-installation.md) and
[the maintained checklist](../../docs/mainline-feature-checklist.md).

## Stock candidate from verified OS inputs

Download the SDK, target and base files from one selected q2-os RC and verify
its Release asset digests and `RELEASE-SHA256SUMS`. Keep the original OS source
revision in its SDK receipt. A later installer can reuse it only when the
Buildroot inputs match. Prepare the stock Wi-Fi and kernel kit once under
`build/`:

```sh
python3 os/compose_stock.py prepare --assets VERIFIED_OS_RELEASE \
  --output build/os/stock-inputs-01
```

The preparation uses the selected production Linux and public RTL8733BS,
wpa_supplicant, libnl, iw, hostapd and dnsmasq sources. It builds the real
driver and WPA2-profile tools. Keep the kit and receipt together. The QEMU
kernel release has the in-tree modules, so the stock preparation builds one
matching kernel tree with the external driver. Subsequent paired player
updates can compose against the same kit and SDK:

```sh
python3 os/compose_stock.py image --assets VERIFIED_OS_RELEASE \
  --stock-inputs build/os/stock-inputs-01 \
  --player-source PLAYER_CHECKOUT --app PAIRED_PLAYER_OUTPUT/q2nd \
  --tsrs-source TSRS_CHECKOUT --tsrsd VERIFIED_TSRS_OUTPUT \
  --qemu-user QEMU_MIPSEL --release-tag V0.01 \
  --output build/os/stock-candidate-01
```

The composer uses the normal installed service overlay and produces a
keyless candidate with `xImage`, `rootfs.squashfs`, `rescue.cpio`,
`shanling-q2.dtb` and `artifact.json`. Reuse across app releases keeps the
exact kernel, rescue, module set and radio tools. A new radio recipe or
incompatible SDK requires fresh stock preparation.

This candidate still requires stock recovery and first-install hardware
qualification. The stock route omits the vendor Bluetooth payload. Review
the embedded Wi-Fi firmware's redistribution terms, corresponding sources
and third-party notices before distributing an image.

The stock kit also places the Buildroot-pinned ISC wireless regulatory
database and its detached kernel signature in the rescue and installed
roots. It retains the upstream license and leaves country selection to the
normal regulatory policy. A kit made without these files must be prepared
again. For later app builds, keep the selected q2-os Release assets pinned
and pass the same verified kit to `compose_stock.py image`. The kit is a
local input pending review of the Wi-Fi firmware's redistribution terms.

After composing the stock candidate, check both packed roots and the
running kernel with the public QEMU executable. The test uses the kernel
ELF retained in the local kit to read back the accepted database.

```sh
python3 os/install/test_stock_regdb.py --image build/os/stock-candidate-01 \
  --kit build/os/stock-inputs-01 --qemu QEMU_SYSTEM_MIPSEL \
  --out build/os/stock-regdb-check-01
```
