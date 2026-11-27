# Stock updater version rules

The stock updater installs a package whose version differs from the
installed one and refuses a package with the same version. It has no "newer
only" rule. `os/hardware/package.py build` therefore takes both the
package's tag and the installed tag, and refuses a package whose tag equals
the installed one.

## Version check

From the stock V1.32 `update_firmware` function at `0x4f8150` (1,072 bytes,
SHA-256 `d08411c0fb9e0c541cf740cf99c7784731d4635e130b38d1f2061309ef7d482b`):

1. The model must match.
2. The version must contain `V`.
3. The version is compared with the installed version by `strcmp`. The
   branch at `0x4f8444` accepts a different version; an equal version returns
   `-14`.
4. The kernel and rootfs checksums in the manifest must match their
   payloads.

This is static code evidence; it has not been exercised by flashing and
rolling back a Q2.

## Choosing a tag

Use a distinct tag for every package: `V` and four letters, digits or dots
(`package.py` checks the form):

```sh
python3 os/hardware/package.py build --kernel KERNEL --rootfs ROOTFS_SQUASHFS \
  --output NEW_OUTPUT/update.tar --tag V0.01 --installed-tag V1.3N
```

Installing the same identity twice is refused by the stock updater; a
package that needs to replace the installed one takes a new tag. The stock
update and recovery flow, including recovery with Previous held at power-on,
is in [boot-chain.md](boot-chain.md).
