# Persistent application update protocol

The installed launcher can select a verified, read-only SquashFS image from
internal state. The root filesystem and its `/usr/bin/q2nd` remain the recovery
baseline. This protocol requires a bootstrap OS containing the updater, loop and
SquashFS support; it is not available merely because an app package exists.
Hardware qualification and installed-release evidence are separate.

The producer supplies an image whose root has an executable `q2nd`, and a
separate, exactly one-line, newline-terminated manifest:

```
Q2APP1 VERSION APP_SHA256 IMAGE_SHA256 OS_RELEASE_SHA256
```

The version is three decimal components separated by dots. Digests are lowercase
SHA-256; the last digest covers the exact bytes of `/etc/q2-os-release.json`.
The image may be at most 8 MiB. The updater checks the copied image digest,
mounts it read-only, verifies the application digest and the bounded
`q2nd --version` output (exactly `VERSION` followed by a newline), and requires at least
2 MiB free internal state (with an additional 16 KiB admission margin). If
space requires it, the inactive previous image can be retired, never the active
image. Incomplete, unreferenced candidate files are removed on a later stage.
Inputs and persistent metadata must be root-owned regular files, not symlinks.

Commands, run as root:

- `q2-app-update capability` prints protocol and limits.
- `q2-app-update status` prints `Q2STATE1 ACTIVE PREVIOUS STAGED PHASE`.
  Identities are image digests, `builtin`, or `none`; phases are `stable`,
  `trial`, and `running`.
- `q2-app-update stage IMAGE MANIFEST` validates and commits a candidate.
- `q2-app-update apply` stops the player under the service control lock,
  records the trial and starts the service. Before executing the candidate the
  launcher records `running`. A reboot before launch also discards the pending
  trial; the launch permission exists only in volatile runtime state. A later
  launch with an unconfirmed `running` trial returns to the confirmed active image or the built-in application.
- `q2-app-update confirm PID START_TICKS IMAGE_SHA256` promotes a running
  trial only when the live process incarnation, mounted executable, player PID,
  ready marker and supervisor-authenticated healthy marker all match. The
  caller must perform its own intended short health gate; time alone does not
  confirm an update.
- `q2-app-update disable` stops the service and selects the built-in player,
  retaining the previously active image for rollback. Use this for the full-OS
  installer preflight when its process-identity check requires the built-in app.
- `q2-app-update rollback` stops the service and selects the previous image,
  or discards a staged or unconfirmed trial without swapping the active image,
  before restarting it.

Persistent selection changes use a single synced, atomically renamed state
record. A second lock serializes state changes with launcher exit; the existing
service control lock protects external lifecycle operations. The image is
mounted at `/run/q2-app` with read-only, executable, `nosuid,nodev` options; the
launcher stops the child and disarms supervision before unmounting. Only a
SquashFS loop mount backed by an updater image may be unmounted there. The
selected app path is also used for process-exit telemetry. Each launch checks
the OS binding and image/application digests again. A missing, damaged or
incompatible selection falls back to the built-in executable.
