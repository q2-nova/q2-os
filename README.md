# Q2 operating system

The Shanling Q2 is a portable music player. This repository builds the Linux
system that runs Q2 Nova on it: the base software and recovery environment, a
library for accessing the hardware, and a software development kit (SDK) for
building programs that run on the Q2. It is for developers who change the
system or build software for the Q2.

To install Q2 Nova on a Q2, start in
[q2-player](https://github.com/q2-nova/q2-player). The [releases
page](https://github.com/q2-nova/q2-os/releases) hosts component prereleases:
the SDK, kernel, rescue image and corresponding source that the player release
builds from. They contain no installable update.

To build the SDK, start with [Build and test the SDK](#build-and-test-the-sdk).

## Documentation

- [Development image in emulation](docs/os-image-build.md)
- [Installed image build](os/install/README.md)
- [SDK releases](docs/sdk-promotion.md)
- [Hardware reference](docs/hardware.md), [boot chain and recovery](docs/boot-chain.md)
  and [stock firmware behaviour](docs/stock-firmware.md)
- The [specification](docs/specification.md) lists what the system must do,
  and the [changelog](CHANGELOG.md) records completed work.

## Build and test the SDK

On a Linux host with Python 3, Git, Podman and network access, run:

```sh
python3 os/build.py container
python3 os/build.py sdk
python3 os/test_sdk.py
```

The builder fetches the exact public Buildroot commit in `os/sources.json` and configures XBurst2, NaN-2008, the Q2 packages and overlay. To use a Git mirror, set `Q2_BUILDROOT_REPO` to a path or URL containing that commit.

The SDK archive is `build/os/out/images/mipsel-buildroot-linux-gnu_sdk-buildroot.tar.gz`. The test relocates the SDK in a temporary directory, compiles and links a time64 C program, and records its ELF ABI in `build/os/sdk-test.json`.

The SDK build uses generated emulator fixtures. For a player-enabled emulator build, stage `q2nd` from a q2-player paired build with `os/rust-build.py --player`; `os/post-build.py` then installs the launcher from `os/launcher.sh`. The emulator uses a separate public QEMU compatibility revision and its own pinned kernel series. See the [installation build guide](os/install/README.md) for stock-device image assembly and its validation requirements.

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

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks a change must pass.
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
Participation follows the [code of conduct](CODE_OF_CONDUCT.md).

## Related repositories

- [q2-player](https://github.com/q2-nova/q2-player): the player app and the installable update. Start there to install Q2 Nova.
- [q2-toolchain](https://github.com/q2-nova/q2-toolchain): the Rust and Clang cross-compilers and the Q2 QEMU emulator.
- [tsrs](https://github.com/q2-nova/tsrs): the Tailscale network daemon.
- Forks this repository builds from: [linux](https://github.com/q2-nova/linux)
  (branch `q2/7.3`), [buildroot](https://github.com/q2-nova/buildroot) (branch
  `q2/2026.08`) and [qemu](https://github.com/q2-nova/qemu) (branch
  `q2/10.1-os-compat` for the development emulator, `q2/10.1` for installed-image
  and kernel fault tests). `os/sources.json` pins the exact commits.

## Licensing

Original standalone Q2 OS code and documentation are licensed under
[GPL-3.0-or-later](LICENSE). This includes the platform bridge, probe tools,
installation utilities and userspace scripts.

Linux sources, kernel modules and device-tree inputs retain their applicable
kernel terms ([GPL-2.0](LICENSES/GPL-2.0.txt)) and individual notices. Patches to Linux, RTL8733BS, Buildroot,
BusyBox and other upstream projects follow the applicable upstream terms.
The IANA time zone table is public domain. The bundled Noto Emoji font keeps
its [SIL Open Font License](os/overlay/usr/share/fonts/noto-emoji/OFL.txt).
The image carries the third-party notices of q2nd, q2-portal and tsrsd in
`/usr/share/licenses/` ([os/install](os/install/README.md#build)).
External SDK and image components have their own terms and notices. See the
[SDK promotion procedure](docs/sdk-promotion.md) before redistributing assets.
