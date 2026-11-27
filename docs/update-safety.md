# Firmware updates and recovery

**Historical note (2026-11-12):** the stock-firmware card mode's app code and build tooling (`platform/q2.rs`, `airplay.rs`, `--device`, `q2/build.sh`, `q2/sysroot.sh`, `q2/deploy.py`, `q2/firmware/`, `q2/card/` except `launcher.sh`) were deleted on 2026-11-12. Where this page names them, it describes the tree before that date.

This page's original V1.3N/V1.3R stock-launcher audit below is historical. For the
currently installed mainline OS and its actual flash/validation evidence, use
[current status](status.md), [mainline status](mainline-status.md) (records to V0.44) and the
[installed service/storage contract](mainline-installed-services.md). Statements
below about an unflashed first package or stock-kernel kexec do not describe the
current mainline installation.

The package has passed host-side checks; it has not been flashed on a Q2
by this project. These checks reduce known risks but cannot guarantee that
a first flash, a faulty card or an interrupted flash will be recoverable.

## What is preserved

The `V1.3N` package SHA-256 is
`309ccd1234af35949844b40343e3b30a8b6b16be1b2608e7ef51ba33b3614d44`.
`q2/firmware/verify_update.py` compares it against the pinned stock V1.32 ZIP:

- The kernel is byte-identical, and the archive contains the same entries
  and metadata. There is no added bootloader image.
- All 3,171 filesystem entries are checked. Only `etc/init.d/S90play` and
  the stock player's version literal change. Other file contents, inode
  metadata, symlinks and hardlink groups match.
- The rootfs remains LZO with 128 KiB blocks, has no added xattr table and
  is 50,429,952 bytes, 12,288 bytes smaller than stock.
- The manifest's kernel and rootfs MD5 checksums match their payloads.
- The stock `update_firmware` function at `0x4f8150`, 1,072 bytes, is
  unchanged (SHA-256
  `d08411c0fb9e0c541cf740cf99c7784731d4635e130b38d1f2061309ef7d482b`).

To repeat it with Python and squashfs-tools installed: Recorded in `8b4908221`
(2026-10-29).

```sh
python3 q2/firmware/verify_update.py 'build/firmware-dl/Q2 Firmware V1.32.zip' dist/firmware/update.tar
```

The audit only extracts files; it never executes the updater or writes flash.

## Future updates

Inspection of the stock updater shows a model check, a version containing
`V`, an exact `strcmp` against the installed version, then kernel/rootfs
checksum checks. The branch at `0x4f8444` accepts a **different** version;
equal versions return `-14`. There is no numeric “newer only” comparison in
that path. The shared version literal becomes `V1.3N`, so stock `V1.32` and
a future `V1.3O` pass its version check. This is static code evidence, not
an on-device flash/rollback test.

For a future change to the launcher firmware, use a distinct five-character
tag in both the player literal and update manifest:

```sh
q2/firmware/mkupdate-podman.sh 'build/firmware-dl/Q2 Firmware V1.32.zip' build/update-next --tag V1.3O --installed-tag V1.3N
```

`--installed-tag` rejects accidental reuse of the current identity. A package
with the same identity is refused by the stock updater; it does not require
forcing an update. Ordinary player changes, including Tailscale, only need
replacement card files. Keep the launcher firmware installed.

## Getting back to the stock player

1. Use **Settings → System → Switch to Stock Player** while the player is working,
   then confirm **Switch Player**. This exits with status 81 and the launcher
   starts stock.
2. Hold **Return** during boot. This is checked early in the player before
   its audio, UI and Tailscale threads start; the key path is untested on Q2.
3. If the player hangs, power the Q2 off completely, remove the microSD card,
   and boot without it. The launcher skips the absent card program and starts
   stock. Alternatively, with the Q2 powered off, use a card reader to rename
   `.q2nd` so the program is absent on the next boot. This fallback does not
   depend on the custom player being able to run.

Host shell tests verify the missing-program path, invalid executable,
three-crash fallback and explicit stock handover. A permanently hung player
does not trigger the crash counter; use the card-removal path after powering
off. These fallbacks rely on a successfully installed, bootable rootfs. They
are not a recovery procedure for interrupted flash writes.

To restore stock firmware, retain `update.orig.tar`, the verified stock V1.32
archive already on the card. When needed, copy it to the root as `update.tar`
and use the stock player's **System settings → System Update → TF card
update**. The backup name itself is not an installable menu filename.

Before the first flash, use a charged battery, safely eject/disconnect USB,
and do not interrupt the update. After first boot, test **Settings → System →
Switch to Stock Player** and stock USB storage before relying on the custom
player. A full flash-back-to-stock test is still needed to prove rollback on
this Q2.

## One-shot development trials

[Recoverable firmware experiments](firmware-trials.md) now provides a RAM-only
userspace pivot whose candidate never becomes the persistent boot default.
The V1.3R candidate strengthens card-readiness handling and has the same
stock kernel/updater integrity guarantees above, with a fresh audit recorded
there. It is built and emulator-tested, not installed. Full alternative-kernel
boot needs additional recovery-kernel/bootloader support; the stock kernel's
kexec entry point is unimplemented.
