# Contributing to Q2 OS

Send changes as pull requests against `main`. Commits on `main` must be
signed. A maintainer merges a pull request once its checks pass.

## Check a change locally

The host checks need Python 3, GCC and a POSIX shell. They are the commands in
[`.github/workflows/host-checks.yml`](.github/workflows/host-checks.yml),
starting with the document checker:

```sh
python3 tools/check_docs.py
```

Run the checks that match the area you changed:

- SDK and Buildroot configuration: [Build and test the SDK](README.md#build-and-test-the-sdk).
- Kernel, drivers and storage: the suites in [Kernel fault suites](docs/kernel-faults.md).
- The emulator image: [Development image in emulation](docs/os-image-build.md).
- The installed image: [Installed image build](os/install/README.md).

## What CI runs

| Workflow | When | What it checks |
| --- | --- | --- |
| OS host checks | every pull request | document links and reference rows, Python entry points and shell syntax, unit tests for the build inputs, launcher, SSH keys and card storage, and the native platform bridge |
| Kernel fault suites | pull requests and pushes to `main` that touch the kernel fault, driver function, storage test or source lock files | the production kernel's driver functions on the host, and microSD, NAND and watchdog fault recovery under QEMU |
| Portal OS checks | pull requests that touch the captive portal scripts or artifact | network script syntax and the DHCP captive portal hook |

## Documentation rules

`tools/check_docs.py` requires every relative link and anchor to resolve. In
the hardware and system references, each table row names a source and a level
(`device-verified`, `emulator`, `datasheet`, `vendor source`, `stock firmware`,
`host` or `unverified`). Reference documents and the specification carry no
dates, process narration or references to people. Each requirement ID in
[docs/specification.md](docs/specification.md) is defined once.

## Releases

A signed `vX.Y.Z-rc.N` tag runs the SDK release workflow. It repeats the host
checks, builds the SDK and target from the source lock, tests SDK relocation,
time64, MIPS ABI and the touchscreen bridge, builds the production kernel and
rescue image, collects corresponding source and notices, and publishes a
prerelease after checking every asset digest.
[SDK releases](docs/sdk-promotion.md) lists the assets and the recovery path
for an interrupted run.

## License

Original Q2 OS code and documentation are contributed under
GPL-3.0-or-later. Patches to Linux, Buildroot and other upstream projects are
contributed under that project's terms.
