# Source-built Q2 OS in emulation

This builds a development image for the X2000 emulator from public sources:
Buildroot, Linux, the QEMU fork and this repository. It is not a hardware
firmware release, and no command in `os/` flashes a device. Display, audio,
wheel, wireless and recovery qualification on hardware are separate from
proving that the source-built userspace runs under QEMU.

## Sources and architecture

`os/sources.json` locks Buildroot 2026.08.1, Linux 7.3, upstream QEMU, Rust
nightly and source archive hashes. The emulator model, guest patch order and
test harnesses are bound by [the compatibility manifest](../os/compat/inputs.json).
Buildroot's package recipes lock package releases and verify source hashes.
Its build container starts from a digest-pinned Debian image; APT package
versions and the resulting container ID must be kept with build evidence
because the package mirror is not a historical snapshot.

The userspace contract is ELF32 little endian MIPS, o32, MIPS32r2, hard float
FP64, NaN2008 and ABS2008, with at most 32-bit atomics. A small Buildroot patch
adds a named Q2 architecture choice so the legacy-NaN XBurst preset cannot
select the wrong libc. The kernel uses its own XBurst2 configuration.
Emulating a P5600-derived CPU does not qualify broader instructions for
physical XBurst2 hardware.

The generated GNU SDK supplies glibc, CRT objects and libgcc. Rust builds its
standard library for the custom target and uses that SDK; it does not link
against extracted vendor libraries or the stock-firmware startup shims. The
link wrapper only repairs a missing NaN2008 flag on Rust's metadata `symbols.o`
after checking that it contains no executable code. `os/audit.py` checks every
staged ELF's architecture and FP ABI and records a file/hash manifest.

Q2 packages in `os/package/` build the probes and supervisor
(`q2-probes`), the KMS display bridge `libq2platform.so` (`q2-platform`) and
`fsck.exfat` from exfatprogs 1.4.3 (`q2-exfat-fsck`), which the USB card
return uses to check a dirty card.

## Build entry points

Run in a fresh checkout. Buildroot, the emulator and the guest kernel use two
build jobs. Host requirements are Python 3.12+ with Pillow, Git, curl, ffmpeg,
OpenSSL, Podman, and the pinned Rust nightly with `rust-src`. Cargo
dependencies must be available for the offline Rust build. The Rust step
needs a [q2-toolchain](https://github.com/q2-nova/q2-toolchain) checkout and,
unless `Q2_X2000=0`, its built X2000 compiler; the player comes from a
[q2-player](https://github.com/q2-nova/q2-player) paired build.

```sh
python3 os/build.py container
python3 os/build.py configure
python3 os/build.py build
python3 os/rust-build.py --toolchain-repo Q2_TOOLCHAIN_CHECKOUT \
  --compiler-work X2000_COMPILER_WORK --player PAIRED_PLAYER_BUILD
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
`os/probes/rust` with the generated SDK and the X2000 compiler of the
q2-toolchain checkout (`Q2_X2000=0` uses the stock nightly instead), and
copies `q2nd` from a q2-player paired build (the output directory of its
`tools/build-target.py`) after checking it against that build's
`result.json`. Both land in `build/os/out/artifacts/`, which the second
Buildroot pass installs into the image; with `q2nd` staged,
`os/post-build.py` also installs `os/launcher.sh` as
`/usr/libexec/q2-launcher`. Without `--player` the image has no player, and
the suites that start one (`os/check.py` runs `test_boot.py --app`) need it.

The kernel and QEMU builds use the container ID in the source lock. Restore the
qualified image from its OCI archive or build it from the QEMU fork's
`tests/q2/Containerfile`. Rebuilding against the current package mirror
requires recording and qualifying the resulting container ID.

The builder fetches the QEMU compatibility revision pinned in `os/sources.json`
from the public fork. Set `Q2_QEMU_REPO` to use a mirror, or pass
`python3 os/emulator.py qemu --qemu-cache PATH` for a local checkout. Kernel
sources are prepared atomically in a revision-specific directory.

An unchanged build reuses the verified binary and receipt. When the QEMU build
inputs change, the builder keeps the old binary and receipt in
`build/os/qemu-artifact-<receipt SHA-256>/` before publishing the new pair.
Run `python3 os/test_dpu.py` and `python3 os/test_watchdog_model.py` against
the built OS kernel image, or pass `--kernel` with a generated paused-machine
uImage under this checkout for model-only controls.

For embedded-initramfs diagnosis, build with
`python3 os/emulator.py kernel --initramfs build/os/out/images/rootfs.cpio.gz`
and test with `python3 os/test_boot.py --initramfs --kernel
build/os/kernel-initramfs/uImage.gz`. The emulator loads a uImage directly;
this does not test ROM, SPL, U-Boot or an on-device update chain.

The recovery and rescue kernel profiles require `--enable NAMESPACES --enable
KEXEC --enable HOTPLUG_CPU --disable CRASH_DUMP`. The kernel builder selects
DTB command-line handling for UHI kexec and keeps the cold-boot fallback.
Prepare the rescue archive with `python3 os/recovery/prepare.py` after
rebuilding the root image, then rebuild its embedded kernel before running
the suites. OS-local kernel patches in `os/patches/kernel/` are applied after
the pinned builder series; their hashes identify the source cache and appear
in kernel manifests and test reports, and tests reject kernels built with a
stale local patch set.

## Image, state and development services

`q2-sd.img` is an MBR disk with a 64 MiB squashfs partition and a 192 MiB ext4
`Q2STATE` partition, padded to a 512 MiB SD capacity. Linux identifies root by
the fixed MBR PARTUUID. State is mounted by label and is never formatted
automatically. Missing, unreadable or unwritable state leaves the diagnostic
shell available and prevents player startup. `/tmp` and `/run` are volatile.

The UART3 console carries boot logs; UART0 provides a development root shell.
Most diagnostic tests run QEMU with no NIC and the container with no network.
The USB network suite permits only two synthetic fixture endpoints. Dropbear
is configured for key authentication and stores its host keys on state; the
image contains no credentials or pre-generated private keys. The local HTTP
server, enabled only with `q2.diagnostic=1`, binds 127.0.0.1 and serves
generated Navidrome responses, artwork and a four-second tone. Its synthetic
credentials are not a production authentication setup.

The default player uses mainline KMS, evdev and ALSA with worker-health
supervision. `q2.diagnostic=1` selects the headless fixture. Emulated
display, input and PCM evidence is separate from physical sound output, input
feel and battery performance.

`q2-app-update CANDIDATE SHA256` stages a checked application replacement on
state and asks the launcher to restart. The launcher keeps the previous
binary, confirms rendered UI (and UI and audio health in the mainline
service), and rolls back a failed or interrupted unconfirmed trial. This is
development app iteration, not a signed OS updater.

## Tests and limits

`python3 os/check.py` runs the suites `core` (ABI audit, an incompatible-object
link test, SD application boots with and without fixed icount, boot-failure
tests, the DPU and watchdog model regressions, exact KMS pixel comparisons
and the initramfs boot), `audio`, `integration`, `boot-watchdog`, `kexec`,
`appliance`, `network` and `bundle`; `--suite NAME` selects one and
`--report NAME.json` keeps a separate report under `build/os`. Each guest
writes console, shell and result logs to its own `build/os/tests/boot-*`
directory. Scripts never report emulated timings as physical performance.

The C probe covers o32, FP behavior, pthreads on both emulated CPUs, TLS,
32-bit atomics, signals, clocks, post-2038 time, large files, dynamic loading
and fork/wait. The Rust probe covers C/Rust floating-point and structure FFI,
callbacks, TLS, threads, atomics, time and file I/O.

Where entropy is needed, the harness injects fresh host `os.urandom` bytes
through a small RNDADDENTROPY helper, after disabling serial command echo.
This is a test fixture: it neither proves a hardware RNG nor establishes
production boot-time entropy readiness. No fixed random seed or private host
key is embedded in the root filesystem.

Reproducibility controls include a fixed source epoch, filesystem UUIDs and
ext4 directory hash seeds, plus a fixed kernel build user, host and version.
Empty state is created deterministically; a booted state image is mutable and
is never compared as a release artifact. The seed option is documented in
upstream [e2fsprogs mke2fs](https://github.com/tytso/e2fsprogs/blob/master/misc/mke2fs.c).

`q2nd --drm-test` (or `Q2ND_PLATFORM=drm-test`) uses standard KMS dumb buffers,
event-driven page flips, backlight and battery sysfs interfaces through
`libq2platform.so`. It rotates the software UI into the 320×375 panel mode.
Input is scripted and audio uses a software sink. Tests compare the central
unmasked UI pixels exactly with QEMU scanout, twice across player restart,
and verify that exit turns the backlight off. The normal service uses the
mainline KMS/evdev/ALSA backend.

The image exercises emulated mainline audio, wheel, USB networking, RTC and
watchdog integration. Physical jack and DAC behavior, wireless, Bluetooth and
suspend/resume are not qualified here. ALSA capture and restricted RNDIS tests
establish emulated data paths, not physical sound or USB electrical behavior.
Battery sysfs readings are emulator stimuli, not measurements. ROM, SPL,
U-Boot, NAND power-cut recovery and a physical recovery path are gates before
hardware boot. Signed remote updates are outside the local one-shot
transaction implemented here.

## Repeating and preserving a build

Build another independent toolchain, root filesystem and Rust output tree
with the same verified source-download cache:

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
diagnostic ext4 root, pristine state and complete SD image. Both trees stage
the same paired player, so it is not compared. The SDK tarball is a separate
development artifact: its host paths and archive metadata are not covered by
the byte-reproducibility claim. Run `relocate-sdk.sh` after extracting it.
C SDK consumers must use `-D_TIME_BITS=64 -D_FILE_OFFSET_BITS=64`, as the
Buildroot package recipes and relocation test do.

The non-executable-stack settings apply to glibc startup code, libc, the loader
and target links. ALSA's libtool build-directory RPATH is disabled because its
libraries use the standard `/usr/lib` search path; this avoids path-length
padding differences without removing ALSA features.

When changing architecture or libc settings, build a new output tree. Buildroot
does not invalidate every affected package. For a local package recipe
change, run its `PACKAGE-dirclean` target before rebuilding; rebuild Rust and
stage its artifacts again when its inputs change. Rebuild the
embedded-initramfs kernel after every rootfs change. The harness verifies
kernel and emulator identity before launching; do not edit cached upstream
sources.

Keep `build/os/{build-manifest,acceptance,reproducibility,sdk-test}.json`,
`out/images/`, the four kernel artifact manifests, the QEMU artifact manifest,
source archives and the build containers with a release checkpoint. Export
the build images as OCI archives with their hashes; restore them with
`podman load -i ARCHIVE` and tag the userspace image `q2-os-build`.

`python3 os/manifest.py` records container, package and compiler identities
and source-file hashes. For source and licensing records:

```sh
python3 os/build.py build legal-info
cargo vendor --locked --offline --versioned-dirs --sync os/probes/rust/Cargo.toml build/os/vendor > build/os/vendor-config.toml
```

Keep `out/legal-info/`, the verified Buildroot archive from `downloads/`, the
repository sources and patches, and vendored Cargo sources together. Buildroot
warns that it does not copy its own sources; the Rust toolchain and its
`rust-src` component are pinned separately. The manifest records Cargo package
notices; generated fixtures contain no personal data. Dependency preparation
may use host networking; guest tests have no external networking.
