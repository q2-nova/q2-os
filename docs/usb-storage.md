# USB microSD handoff

`q2-usb-storage` (source [os/hardware/usb-storage](../os/hardware/usb-storage),
installed as `/sbin/q2-usb-storage`) lends the microSD card to a USB host as a
mass-storage LUN and takes it back. The card has one owner at a time: mounted
on the Q2, detached during a handoff, or exported to the host. Export stops
playback. The helper never formats, repartitions or repairs the card, and
never reads its files.

The automatic manager that starts and ends exports on cable events, the
drive-mode screen, and the qualification state are in
[usb-host.md](usb-host.md). The player's USB Drive setting and its policy are
documented in q2-player.

## Commands

```sh
q2-usb-storage start                  # export the card
q2-usb-storage status
q2-usb-storage stop [--defer-restart] # return it after host eject or unplug
q2-usb-storage retry-export           # rebind a detached, armed export
q2-usb-storage disconnected           # 0 when physical VBUS loss is confirmed
```

All need root and take the lock directory `/run/q2/usb-storage.lock`; a held
lock exits 75. A stale lock after a kill or power loss needs inspection and is
never removed automatically.

- `start` briefly re-enumerates USB while adding the mass-storage function
  beside RNDIS, which keeps its addresses. Run it from Wi-Fi SSH, or detached
  with output under `/run`, so losing the USB SSH session cannot interrupt it.
- `stop` returns the card when the host has ejected it (the LUN has no
  backing file) or when physical USB removal is confirmed. Otherwise it exits
  75 and the card stays with the host.
- `stop --defer-restart`, installed profile only, returns the card and holds
  the player behind the setup screen; `q2-installed-services resume` releases
  it. The hold is in RAM.
- `retry-export` rebinds the gadget after a failed bind left the LUN armed.
  It cannot clear an occupied LUN.
- `disconnected` runs the same physical-absence check as `stop`, exiting 75
  on an unconfirmed sample. It does not authorize a mount.

## Layouts

The helper accepts only these layouts and checks them again on return:

- **Card:** `/dev/mmcblk0` is an SD card below MSC2 (`13490000.mmc`), with
  its CID recorded and compared on return; partition 1 is exFAT at
  `/mnt/mmc`.
- **Installed profile:** `/state` is a UBIFS bind mount of `/q2-mainline`
  from UBI volume `user_data` on the `userdata` MTD partition. It stays
  mounted during export; mountinfo, the volume and partition identity are
  checked against the saved session.
- **RAM image profile:** `/state` is ext4 on a loop device backed by the
  state image on the card ([PLAYER.md](../os/hardware/PLAYER.md)). It is
  unmounted and its loop detached before export.
- **Gadget:** an existing `q2diag` or `q2net` RNDIS gadget owns the
  controller.
- **Player:** installed services use `/sbin/q2-installed-services`, image
  services `S80q2-player`; a manual RAM player must have its PID in
  `/run/q2/player.pid`. An unknown or surviving player process is a refusal.

## Export sequence

1. Validate the card, layout and gadget; record the session under
   `/run/q2` (volatile, not crash-consistent).
2. Mark state unavailable (`/run/q2/state-status` is `usb-storage`), so the
   launcher cannot restart, and stop the player: the service stop disarms the
   supervisor and waits; a manual player gets TERM with a bounded wait, never
   KILL.
3. Start the drive-mode screen. If its first frame fails, the export aborts.
4. `sync`; in the RAM profile unmount `/state` and detach its loop (an
   autocleared loop is accepted once its backing file is gone); unmount the
   card. No forced or lazy unmount is used.
5. Check that nothing holds the card: mounts in any readable process mount
   namespace, block-layer holders, card-backed loops, swap, and open file or
   block-device descriptors including aliases. An unreadable namespace or
   descriptor of an active process is a refusal; a descriptor number reused
   during the scan is read once more. Flush the card's block caches.
6. Unbind the gadget, add `mass_storage.q2card` with `removable=1`,
   `nofua=0`, `ro=0`, `cdrom=0` (the stock `storage_config.sh` removable and
   FUA settings), link it into the configuration and repeat the holder
   check.
7. Set the LUN's backing file to the whole card and bind the gadget.

## Return sequence

1. A non-empty backing file requires confirmed physical unplug: the AXP2101
   `axp20x-usb` `online` value must read exactly 0 three times, 100 ms
   apart. UDC state, a bus reset or the helper's own unbind does not count.
2. Unbind the gadget; configfs waits for the function's unbind, which drains
   pending transfers. Close only the saved card's backing file, `sync` the
   received writes and remove the function. `forced_eject` is never used, and
   a backing file that is not the saved card is refused.
3. Rebind RNDIS alone, after the LUN is gone, so a reconnect during teardown
   cannot reach the card.
4. Flush the block caches and mount the card. If its exFAT VolumeDirty flag
   is set and `fsck.exfat -n` fails, it is mounted read-only and
   `/run/q2/card-readonly` is created. The image's `fsck.exfat` comes from
   the `q2-exfat-fsck` package (exfatprogs 1.4.3). On an image without it, a
   dirty card is mounted writable without a check, `/run/q2/card-unchecked`
   is created and the helper says so on stderr.
5. Restore the RAM profile's state image, or check the internal state is the
   saved one; set `ready` and restart the player (or hold it with
   `--defer-restart`). The installed player restarts to reload the
   host-edited configuration.

Failures leave the card detached and the player blocked until `stop` can
restore the known layout. A missing or changed card, changed layout, missing
state image or failed remount never leads to formatting, repair or another
partition. A failure after the gadget is unbound can leave USB management
down until a Wi-Fi `stop` or `retry-export`. Host writes that were never sent
before an unplug are lost. Do not cut power while a powered host owns the
LUN.

This is a single-appliance protocol: other privileged tools must not mount,
open or export the card while the host owns it.

## Build and tests

`os/hardware/player.config` builds `CONFIG_USB_CONFIGFS_MASS_STORAGE=y`;
`build-player.py` installs and hashes the helper for the RAM image, and
`os/install/build.py` packages it with the manager and screen for the
installed image.

```sh
python3 os/test_usb_storage.py
sh -n os/hardware/usb-storage
```

The host tests use generated filesystem, process and configfs fixtures with
mocked mount and loop commands. They cover ordered detach, eject and return;
refusal on UDC unbind or reset; confirmed and transient unplug samples;
retry binding; busy unmount and loop detach; foreign namespaces, loops, swap,
holders and descriptors; wrong controller or layout; a changed card; a missing
state image; a failed remount; concurrent and stale locks; untracked players;
races with exiting processes; autocleared loops; and the internal-state
restart. They do not exercise a USB controller, host cache or SCSI exchange.
