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

## USB SSH from a microSD card

On a normal installed image, put your OpenSSH public key in a file named
`authorized_keys` at the root of the microSD card. Keep the private key on
your computer. The file accepts one public key per line, like a normal
OpenSSH `authorized_keys` file, using options supported by Dropbear.

Boot the Q2 with that card and connect it over USB. Use the host and device
addresses in the [USB network setup](os/hardware/README.md) and connect
with `ssh root@<device-address>`. The Q2 generates
its own persistent host key. The first connection will show a new host
fingerprint.

The Q2 reads the card file once per boot. Remove the file and reboot to
revoke access. USB drive export can briefly interrupt the USB connection
while the device re-enumerates. Reconnect SSH afterward if needed.

## Licensing

Original standalone Q2 OS code and documentation are licensed under
[GPL-3.0-or-later](LICENSE). This includes the platform bridge, probe tools,
installation utilities and userspace scripts.

Linux sources, kernel modules and device-tree inputs retain their applicable
kernel terms and individual notices. Patches to Linux, RTL8733BS, Buildroot,
BusyBox and other upstream projects follow the applicable upstream terms.
The IANA time zone table is public domain. The bundled Noto Emoji font keeps
its [SIL Open Font License](os/overlay/usr/share/fonts/noto-emoji/OFL.txt).
External SDK and image components have their own terms and notices. See the
[SDK promotion procedure](docs/sdk-promotion.md) before redistributing assets.
