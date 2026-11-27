# Connected hardware diagnostic (work in progress)

This directory prepares normal `update.tar` installation, USB networking and
later OS updates, with Previous + Center stock recovery preserved. No artifact
here is hardware-qualified yet.

The existing `os/image.py` SD image and `uImage.gz` are emulator artifacts. The
stock SPL loads a uImage payload and jumps into it; it does not provide the
normal U-Boot decompression service. Use the MIPS self-decompressor (`uzImage.bin`,
uImage compression `none`) and an embedded Q2 DT instead. An image fitting the
6 MiB kernel slot is necessary but not sufficient.

Initial diagnostic scope: one CPU, inherited clocks (`clk_ignore_unused`), USB
RNDIS with key-only SSH, optional serial, RAM logs and no player. First qualify
this minimal boot profile without exposing NAND/SD/PMIC policy. The subsequent
update profile must additionally qualify SD staging and access to only the `ota`
selector; it must never write main slots directly. Stock recovery does those
writes. A minimal profile without those devices cannot yet self-update.

Hardware USB address: device `192.168.77.2/30`, host `192.168.77.1/30`; no DHCP,
DNS or default route. The emulator test subnet is an explicit separate profile.
Provide an SSH **public** key outside Git when building. The build reads no
private key or configuration from a used device. Host-key generation requires initialized kernel entropy; any
emulator entropy injection is a synthetic fixture, never a hardware entropy claim.

Before hardware: exact-package SPL boot, USB/SSH transfer and reconnect, normal
recovery install, next-version install, corrupt-main key recovery, stock restore,
protected-partition comparison and durability/failure checks must pass. The
diagnostic kernel has its own pin; a diagnostic boot does not qualify the full
player.

## Build and host checks

Use the generated target of the stable Buildroot build (`os/build.py build`).
Only BusyBox, Dropbear and their dynamic libraries are copied; their hashes are
recorded. The diagnostic kernel has its own pinned source profile and
resolves/audits its own config. It does not overwrite the existing OS pin or another worktree.

```
python3 os/hardware/build-diagnostic.py --buildroot-target BUILDROOT_OUT/target \
  --authorized-key GENERATED_OR_EXPLICITLY_SUPPLIED_PUBLIC_KEY
python3 -m unittest discover -s os/hardware -p 'test_*.py' -v
python3 os/hardware/package.py build --kernel build/os/hardware/xImage \
  --rootfs ROOTFS_SQUASHFS --output NEW_OUTPUT/update.tar \
  --tag V0.01 --installed-tag V1.3N
python3 os/hardware/package.py check NEW_OUTPUT/update.tar --sha256 TRUSTED_SHA256
```

`package.py` enforces exact member names/types, model/version, legacy MD5,
uImage header/payload CRC and executable format, RAM bounds, squashfs header
and partition limits. These are container checks, not proof that arbitrary
kernel instructions or a filesystem are safe. SHA-256 must come from a trusted
release record; hashing an untrusted download does not authenticate it.

`package.py stage update.tar HOST_CARD_ROOT --sha256 TRUSTED_SHA256` validates
before writing, rejects an existing `recovery-update` directory, writes/fsyncs
into a sibling temporary directory, then renames and fsyncs the card directory.
It never changes `ota`, opens a device, or reboots. It is **host card preparation**. For on-device staging, use `q2-hw-update`
in the update profile below. A stale lock after an interrupted preparation
requires inspection; it is not automatically discarded. Filesystem durability
must still be tested on the actual card/filesystem before claiming power-cut
atomicity. Keep stock restoration payloads elsewhere: recovery deletes its
staging directory after applying an update.

## Rehearse without hardware

```
python3 os/hardware/rehearse.py --nand SANITIZED_MKFLASH_NAND \
  --qemu BOOT_CHAIN_QEMU --key GENERATED_FIXTURE_PRIVATE_KEY
```

The input must be a generated full-chip data+OOB NAND with erased identity and
userdata. This copies the executable kernel into a disposable fixture, boots
through the real SPL, checks SSH, a bidirectional 131071-byte transfer and USB
reconnect. `--icount` selects instruction-counted emulation. The fixture's SSH
identity is generated specifically for the test; never supply another key.
Known-host records are isolated under the run directory. The optional `--fixture-entropy` credits fresh host random bytes over the
emulator UART using BusyBox seedrng and records this in the result. It allows
deterministic runs to test SSH without pretending to validate physical entropy.
Without it the runner waits on the guest's own CRNG. Neither mode establishes
physical entropy quality.

Add `--recovery-package PACKAGE` to stage its two payloads on a generated FAT32
card and set the selector in the **emulated** NAND before boot. Stock recovery
then performs the real erase/write/reboot sequence. This bypasses the stock menu
UI/archive parser, so it does not establish that menu installation passed. The
runner checks kernel/rootfs bytes, erased selector and protected partitions.

## Remaining hardware gates

- Qualify the self-decompressor's cache transition and memory ranges on XBurst2.
  Mainline `arch/mips/boot/compressed/decompress.c` explicitly leaves cache
  flushing as a FIXME; the functional emulator does not model cache coherency.
- Qualify physical USB enumeration/reconnect, host-key provisioning/entropy and
  host networking configuration. The USB helper disables IPv6 on the
  management interface to prevent router-advertisement configuration.
- The SD/`ota` profile and on-device staging/apply command pass an emulated
  recovery round trip. Physical durability, stock rollback and corrupt-main
  recovery remain separate qualification steps; the no-storage profile cannot
  apply updates.
- Record recovery's observed userdata changes. Stock recovery may reset
  userdata on a UBI/UBIFS failure; preserving userdata is not a release gate.
- Resolve/keep separate the full player baseline's black-KMS failure; qualify
  watchdog behavior and hardware power policy independently.

## Wi-Fi and on-device updates

`--wifi` adds the pinned RTL8733BS module plus matching cfg80211, MMC/SDHCI and
regulator dependencies. `q2-wifi start|stop|status` manages explicit post-boot
loading. `--wifi --updates` also adds manual SD/ota transport, mounted using
`q2-update-storage`; it is not activated by init. These profiles use XZ kernel
compression to keep their embedded module set within the 6 MiB slot.

`q2-hw-update check|stage|apply|status` is included in the diagnostic. The
no-storage profile supports package checking but cannot stage/apply to
hardware without its transport. Hardware cache/boot qualification is still
open.


`--update-package PACKAGE` exercises the production on-device command through
USB upload, private staging (including source and copied-archive validation),
selector write, watchdog reset, SPL, stock recovery,
and return to diagnostic SSH. `--transport --wifi` additionally checks that
stopping Wi-Fi leaves the mounted SD card and its files accessible. These tests
use disposable NAND/card images and generated SSH identities only.


For a physical trial, `--host-key GENERATED_DROPBEAR_HOST_KEY` embeds a newly
generated device-specific SSH host key. Pin its public key on the client
before installation. Never supply an existing private key or distribute this
per-device image.
The update transport supports both FAT and exFAT cards; Wi-Fi and storage remain
manually loaded. `rehearse.py --card-filesystem exfat --update-package PACKAGE`
exercises the exFAT path using a generated card populated over guest USB SSH.

Both RAM and installed image builders reject OpenSSH private-key input for
`--host-key`. Supply native Dropbear format: generate it with `dropbearkey`, or
convert an explicitly generated OpenSSH key with
`dropbearconvert openssh dropbear INPUT OUTPUT`. Conversion must preserve its
public identity. The builders run the generated target's actual `dropbearkey`
reader under `build/qemu/qemu-mipsel-static` (override with `--qemu-user`) before
image mutation and again after copying. Build output and provenance contain only
the public algorithm/fingerprint, never private key bytes or parser diagnostics.
Generated validation: `python3 os/hardware/test_hostkey.py`.
