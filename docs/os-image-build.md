# Source-built Q2 OS in emulation

This is a development image, not a hardware firmware release. The device owner authorized
emulation only until a physical recovery path exists. No command in `os/`
flashes a device. Display, audio, wheel, wireless and recovery qualification are
separate from proving that our source-built userspace runs under QEMU.

## Sources and architecture

`os/sources.json` locks Buildroot 2026.08.1, Linux 7.3, upstream QEMU, Rust
nightly and source archive hashes. The adopted model, guest patch order and
test harnesses are bound by [the compatibility manifest](../os/compat/inputs.json).
Buildroot's package recipes lock package releases and verify
source hashes. Its build container starts from a digest-pinned Debian image;
APT package versions and the resulting container ID must be retained with build
evidence because the package mirror itself is not a historical snapshot.

The userspace contract is ELF32 little endian MIPS, o32, MIPS32r2, hard float
FP64, NaN2008 and ABS2008, with at most 32-bit atomics. A small Buildroot patch
adds a named Q2 architecture choice so the existing legacy-NaN XBurst preset
cannot silently select the wrong libc. The kernel uses its independent XBurst2
configuration. Emulating a P5600-derived CPU does not qualify broader
instructions for physical XBurst2 hardware.

The generated GNU SDK supplies glibc, CRT objects and libgcc. Rust builds its
standard library for the custom target and uses that SDK; it does not link
against extracted vendor libraries or the stock-firmware startup shims. The
link wrapper only repairs a missing NaN2008 flag on Rust's metadata `symbols.o`
after checking that it contains no executable code. `os/audit.py` checks every
staged ELF's architecture and FP ABI and records a file/hash manifest.

## Build entry points

Run in the fresh OS worktree. Buildroot, the emulator and the guest kernel use
two build jobs. Host requirements are Python 3.12+ with Pillow, Git, curl,
ffmpeg, OpenSSL, Podman, and the pinned Rust nightly with `rust-src`. Existing
Cargo dependencies must be available for the explicitly offline Rust build.
Recorded in `cca2dd5d2` (2026-10-31).

```sh
python3 os/build.py container
python3 os/build.py configure
python3 os/build.py build
python3 os/rust-build.py --toolchain-repo Q2_TOOLCHAIN_CHECKOUT \
  --compiler-work X2000_COMPILER_WORK --player PAIRED_PLAYER_BUILD
# Stage the approved matching launcher as build/os/out/artifacts/q2-launcher.
python3 os/build.py build
python3 os/image.py
python3 os/audit.py
python3 os/emulator.py qemu
python3 os/emulator.py kernel --enable KEXEC --disable CRASH_DUMP
python3 os/emulator.py kernel --initramfs build/os/out/images/rootfs.cpio.gz
python3 os/emulator.py kernel --output kernel-recovery --enable NAMESPACES --enable KEXEC --enable HOTPLUG_CPU --disable CRASH_DUMP
python3 os/recovery/prepare.py
python3 os/emulator.py kernel --output kernel-rescue --initramfs build/os/recovery/rescue.cpio.gz --enable NAMESPACES --enable KEXEC --enable HOTPLUG_CPU --disable CRASH_DUMP
python3 os/check.py
python3 os/build.py sdk
python3 os/test_sdk.py
python3 os/manifest.py
```

`os/rust-build.py` builds the Rust ABI probe `q2-rust-probe` from
`os/probes/rust` with the generated SDK and the X2000 compiler of a
q2-toolchain checkout (`Q2_X2000=0` uses the stock nightly instead), and
copies `q2nd` from a q2-player paired build (the output directory of its
`tools/build-target.py`) after checking it against that build's
`result.json`. Both land in `build/os/out/artifacts/`, which the second
Buildroot pass installs into the image. Without `--player` the image has
no player.

The kernel and QEMU builds use the container ID in the source lock. Restore the
qualified image from its OCI archive or see the QEMU fork's
`tests/q2/Containerfile` recipe. Rebuilding against the current package mirror
requires recording and qualifying the resulting container ID.

The builder fetches the QEMU compatibility revision pinned in `os/sources.json`
from the public fork by default. Set `Q2_QEMU_REPO` to use a mirror. For a local checkout,
`python3 os/emulator.py qemu --qemu-cache PATH` is equivalent. The build
uses the committed machine and tests on the historical branch. Kernel sources
are prepared atomically in a revision-specific directory.

An unchanged build reuses the verified binary and receipt. When the QEMU build
inputs change, the builder keeps the old binary and receipt in
`build/os/qemu-artifact-<receipt SHA-256>/` before publishing the new pair.
Run `python3 os/test_dpu.py` and `python3 os/test_watchdog_model.py` against
the built OS kernel image, or pass `--kernel` with a generated paused-machine
uImage under this checkout for model-only controls.

For embedded-initramfs diagnosis, build with
`python3 os/emulator.py kernel --initramfs build/os/out/images/rootfs.cpio.gz`
and test with `python3 os/test_boot.py --initramfs --kernel
build/os/kernel-initramfs/uImage.gz`. The emulator directly loads a uImage;
this does not test ROM, SPL, U-Boot or an on-device update chain.

## Image, state and development services

`q2-sd.img` is an MBR disk with a 64 MiB squashfs partition and a 192 MiB ext4
`Q2STATE` partition, padded to a 512 MiB SD capacity. Linux identifies root by the fixed MBR PARTUUID. State is
mounted by label and is never automatically formatted. Missing, unreadable or
unwritable state leaves the diagnostic shell available and prevents player
startup. `/tmp` and `/run` are volatile.

The UART3 console carries boot logs; UART0 provides a development root shell.
Most diagnostic tests run QEMU with no NIC and the container with no network.
The USB network suite permits only two synthetic fixture endpoints. Dropbear
is configured for key authentication and stores its host keys on state; the
image contains no owner credentials or pre-generated private keys. The local
HTTP server, enabled only with `q2.diagnostic=1`, binds 127.0.0.1 and serves generated Navidrome responses,
artwork and a four-second tone. This image's synthetic credentials must never
be confused with a production authentication setup.

The default player uses mainline KMS, evdev and ALSA with worker-health
supervision. Diagnostic boots select the earlier headless fixture with
`q2.diagnostic=1`; stock-firmware defaults remain unchanged when the OS service
is not used. Emulated display/input/PCM evidence is separate from physical
sound output, input feel and battery performance. See the
[appliance checkpoint](os-appliance-integration.md) for the current service,
USB/time policy and paired OS trials.

`q2-app-update CANDIDATE SHA256` stages a checked application replacement on
state and asks the launcher to restart. The launcher retains the previous
binary, confirms rendered UI (and real UI/audio health in the mainline service),
and rolls back a failed trial or
an interrupted unconfirmed trial. This is development app iteration, not a
signed OS updater. A physical release still needs a proven recovery path and
an independently reviewed whole-image update design.

## Test evidence and limits

Current checkpoint results and artifact identities are recorded in [appliance
integration](os-appliance-integration.md). [OS image
validation](os-image-validation.md) preserves the initial checkpoint. Recorded
in `37ea9f3e0` (2026-11-01).

The C probe covers o32, FP behavior, pthreads on both emulated CPUs, TLS,
32-bit atomics, signals, clocks, post-2038 time, large files, dynamic loading
and fork/wait. The Rust probe covers C/Rust floating-point and structure FFI,
callbacks, TLS, threads, atomics, time and file I/O.

Where entropy is needed, the harness injects fresh host `os.urandom` bytes
through a tiny RNDADDENTROPY helper, after disabling serial command echo.
This is an explicit test fixture: it neither proves a hardware RNG nor
establishes production boot-time entropy readiness. No fixed random seed or
private host key is embedded in the root filesystem.

Kernel/emulator milestones are checked at integration boundaries. Temporary
notes use `build/os-handoff/{os-to-kernel,kernel-to-os}/` in the main checkout;
durable contracts stay in the repository. The OS compatibility recipes consume
[checked-in Linux and QEMU inputs](../os/compat/README.md) for the adopted
`47f65aa6` milestone, with the existing local audio-position and watchdog-restart
patches layered on afterward. The original display checkpoint used `fcd20259`;
the later ALSA checkpoint used `2b7b919`. Their reports remain historical.
Audio and wheel integration are now exercised by the acceptance runner. Newer
clock/MMU/storage work is recorded in the appliance document for separate adoption.

Reproducibility controls include a fixed source epoch, filesystem UUIDs and
ext4 directory hash seeds, plus a fixed kernel build user/host/version. Empty
state is created deterministically; a booted state image is intentionally
mutable and should never be compared as if it were a release artifact. The
seed option is documented in upstream
[e2fsprogs mke2fs](https://github.com/tytso/e2fsprogs/blob/master/misc/mke2fs.c).

`python3 os/check.py` runs the ABI audit, an intentionally incompatible-object
link test, normal and fixed-icount SD application tests, boot-failure tests, the adopted
DPU model regression, exact KMS pixel comparisons and initramfs boot. Each guest
writes console, shell and result logs to its own `build/os/tests/boot-*`
directory. Scripts never report emulated timings as physical performance.
`python3 os/manifest.py` records container/package/compiler identities and
source-file hashes; Buildroot's `legal-info` target records package licensing
and source notices separately. The repository-local app/probes have no declared
license grant; the generated inventory must not invent one.

## Display integration and remaining interfaces

`q2nd --drm-test` (or `Q2ND_PLATFORM=drm-test`) uses standard KMS dumb buffers,
event-driven page flips, backlight and battery sysfs interfaces through
`libq2platform.so`. It rotates the software UI into the 320×375 panel mode.
Input is scripted and audio uses a software sink. Tests compare the central
unmasked UI pixels exactly with QEMU scanout, twice across player restart,
and verify that exit turns the backlight off. This is an explicit integration
test mode; the normal service uses the mainline KMS/evdev/ALSA backend.

The image exercises emulated mainline audio, wheel, USB networking, RTC and
watchdog integration. Physical jack/DAC behavior, wireless, Bluetooth and
suspend/resume remain unqualified. ALSA capture and restricted RNDIS tests
establish emulated data paths; they do not establish physical sound or USB
electrical behavior. Battery sysfs readings are emulator stimuli, not physical
measurements. ROM/SPL/U-Boot, NAND power-cut recovery and a proven physical
recovery path remain gates before hardware boot. Signed remote updates are
also outside the local one-shot transaction implemented here.

## Repeating and preserving a build

Build another independent toolchain/rootfs and Rust output tree, using the
same verified source-download cache:

```sh
python3 os/build.py --output repeat configure
python3 os/build.py --output repeat build
python3 os/rust-build.py --output repeat --toolchain-repo Q2_TOOLCHAIN_CHECKOUT \
  --compiler-work X2000_COMPILER_WORK --player PAIRED_PLAYER_BUILD
python3 os/build.py --output repeat build
python3 os/image.py --output repeat
python3 os/audit.py --output repeat
python3 os/compare.py
```

`compare.py` compares the Rust probe, squashfs, compressed initramfs,
diagnostic ext4 root, pristine state and complete SD image. Both trees
stage the same paired player, so it is not compared. The SDK tarball is
a separate development artifact: its host paths and archive metadata are not
covered by the byte-reproducibility claim. Run `relocate-sdk.sh` after extracting
it. C SDK consumers must use `-D_TIME_BITS=64 -D_FILE_OFFSET_BITS=64`, as the
Buildroot package recipes and relocation test do.

The non-executable-stack settings apply to glibc startup code, libc, the loader
and target links. ALSA's libtool build-directory RPATH is disabled because its
libraries use the standard `/usr/lib` search path; this avoids path-length
padding differences without removing optional ALSA features.

When changing architecture/libc settings, build a new output tree. Buildroot
does not automatically invalidate every affected package. For a local package
recipe change, explicitly run its `PACKAGE-dirclean` target before rebuilding;
rebuild Rust and stage its artifacts again when its inputs change. Rebuild the
embedded-initramfs kernel after every rootfs change. The harness verifies kernel
and emulator identity before launching; do not edit cached upstream sources.

Preserve `build/os/{build-manifest,acceptance,reproducibility,sdk-test}.json`,
`out/images/`, all four kernel artifact manifests, the QEMU artifact manifest,
source archives and the build containers with a release checkpoint. This run
also exported both public build images as OCI archives with hashes restore with
`podman load -i ARCHIVE` and tag the userspace image `q2-os-build`. For a fresh
emulator build, supply the pinned upstream sources and the compatibility files
from this checkout. Recorded in `cca2dd5d2` (2026-10-31).

For source and licensing records:

```sh
python3 os/build.py build legal-info
cargo vendor --locked --offline --versioned-dirs --sync os/probes/rust/Cargo.toml build/os/vendor > build/os/vendor-config.toml
```

Keep `out/legal-info/`, the verified Buildroot archive from `downloads/`, the
repository sources and patches, and vendored Cargo sources together. Buildroot
warns that it does not copy its own sources and that the local Q2 packages lack
a declared license file; these are recorded limitations, not license grants.
The Rust toolchain and its `rust-src` component are pinned separately. The
manifest records Cargo package notices; generated fixtures contain no owner
data. Dependency preparation may use host networking; guest tests remain
isolated with no external networking.

## Mainline audio integration

The earlier audio checkpoint adopted mainline ALSA milestone `2b7b919`; see
[interfaces, tests and limitations](os-audio.md). At that checkpoint,
`os/check.py` defaulted to core plus audio acceptance. The core suite adds the watchdog model
regression to the original nine stages. Use `--report NAME.json` to retain
separate suite reports under `build/os`; concurrent runs have distinct IDs.
The mainline player test enables ALSA explicitly with `Q2ND_DRM_ALSA=1`.
That checkpoint kept the default service headless; the current appliance
checkpoint uses mainline mode. Earlier checkpoint hashes in
[os-image-validation.md](os-image-validation.md) are historical and retained.

## Mainline input and kernel handoff

See [mainline integration](os-mainline-integration.md) for evdev/RTC, early
watchdog ownership and actual Buildroot kernel replacement. Additional suites:

```sh
python3 os/check.py --suite integration --report acceptance-final-integration.json
python3 os/check.py --suite boot-watchdog --report acceptance-final-boot-watchdog.json
python3 os/check.py --suite kexec --report acceptance-final-kexec.json
```

The recovery and rescue profiles require `--enable NAMESPACES --enable KEXEC
--enable HOTPLUG_CPU --disable CRASH_DUMP`. The kernel builder selects DTB
command-line handling for UHI kexec while retaining cold-boot fallback. Prepare
the rescue archive with `python3 os/recovery/prepare.py` after rebuilding the
root image, then rebuild its embedded kernel before running the suites.

The earlier mainline checkpoint used five suites (43 stages). The appliance
checkpoint adds three suites; `all` now runs 64 stages. OS-local kernel patches
in `os/patches/kernel/` are applied after the pinned builder series; their hashes
identify the source cache and appear in kernel manifests and test reports.
The coherent audio-pointer patch fixes false underruns found by full-song
normal-time playback. Tests reject kernels built with a stale local patch set.

## Mainline appliance checkpoint

The new [appliance integration](os-appliance-integration.md) makes mainline
KMS/evdev/ALSA the ordinary boot path. The older headless test behavior now
requires `q2.diagnostic=1`. Build the disk kernel with:

```sh
python3 os/emulator.py kernel --enable KEXEC --disable CRASH_DUMP
```

The disposable SD is now 512 MiB with 192 MiB state. The immutable root and state
start offsets are unchanged. New suites are `appliance`, `network` and `bundle`;
`os/check.py` includes them in `all`. Network tests launch only their own generated,
pinned Navidrome fixture. See the appliance document for time provisioning,
one-shot update semantics and the still-unqualified stock bootstrap boundary.
