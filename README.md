# Q2 operating system

This repository owns the Q2 Buildroot external tree, userspace, provisioning, platform library and recovery assembly. The Buildroot fork owns changes to Buildroot itself. The Linux fork contains the production kernel source.

## Build and test the SDK

On a Linux host with Python 3, Git, Podman and network access, run:

```sh
python3 os/build.py container
python3 os/build.py sdk
python3 os/test_sdk.py
```

The builder fetches the exact public Buildroot commit in `os/sources.json` and configures XBurst2, NaN-2008, the Q2 packages and overlay. To use a Git mirror, set `Q2_BUILDROOT_REPO` to a path or URL containing that commit.

The SDK archive is `build/os/out/images/mipsel-buildroot-linux-gnu_sdk-buildroot.tar.gz`. The test relocates the SDK in a temporary directory, compiles and links a time64 C program, and records its ELF ABI in `build/os/sdk-test.json`.

The SDK build uses generated emulator fixtures. For a player-enabled emulator build, supply both a matching `q2nd` and `q2-launcher` in `build/os/out/artifacts/`. The emulator uses a separate public QEMU compatibility revision and its own pinned kernel series. The stock-device installation recipes still need a distributable first-owner identity and rescue strategy.
