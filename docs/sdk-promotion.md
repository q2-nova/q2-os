# Buildroot SDK releases

Pushing a signed `vX.Y.Z-rc.N` tag builds the SDK and matching Buildroot
target from that commit, then builds the pinned production Linux kernel,
keyless rescue image, device tree and matching modules. The workflow first qualifies SDK relocation, time64,
MIPS ABI and the touchscreen bridge. It collects corresponding sources and
notices, then uploads a draft, verifies GitHub's asset sizes and digests
against local bytes and publishes the intact draft as a prerelease.

A manual **SDK release** dispatch with `operation=validate` runs host checks.
Selecting `qualify` builds the SDK and matching target as Actions artifacts.
Manual dispatches do not create GitHub Releases.

A downstream release locks the q2-os RC tag and verifies the Release asset
digests and the checksum manifests before using the files. The prerelease
contains:

- `mipsel-buildroot-linux-gnu_sdk-buildroot.tar.gz`, `sdk-manifest.json`,
  `sdk-test.json`, `buildroot.config`, and `SHA256SUMS`
- `target.tar.gz` and `platform.json`, bound to the exact SDK and configuration
- `xImage`, `rescue.cpio`, `shanling-q2.dtb`, `linux.config`,
  `kernel-modules.tar.gz` and `kernel-base.json`, bound to the userspace platform
- `buildroot-legal-info.tar.gz` with corresponding Linux, Buildroot and q2-os
  source and license data
- `RELEASE-SHA256SUMS` covering the other files

The player and standalone component repos use the SDK without depending on
each other. After the player and standalone TSRS are built, the player
release workflow can call the tagged q2-os checkout's composer:

```sh
python3 Q2_OS/os/compose_ci.py \
  --assets VERIFIED_Q2_OS_RELEASE_FILES \
  --player-source PAIRED_PLAYER_SOURCE --app PAIRED_ARTIFACTS/q2nd \
  --tsrs-source SELECTED_TSRS_SOURCE --tsrsd VERIFIED_TSRS_ARTIFACTS \
  --release-tag V0.01 --output Q2_OS/build/os/ci-installed-image
```

Use absolute paths with the command above. The composer verifies and reuses the published kernel and rescue bytes,
stages their matching modules and emits a keyless QEMU candidate. It accepts the selected TSRS source checkout and verifies the
daemon receipt against it and the pinned upstream TSRS inputs. The image is
for generated local or CI QEMU testing.
