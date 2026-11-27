# OS compatibility build inputs

The OS test image uses Linux compatibility patches adopted from
`47f65aa6c9507cf3f4533d498df2eb1f90a30270`. The SPL-entry diagnostic adds
the kernel series adopted from `d89c735f7b90547c4196475c7b0343a5271e87d6`
or `cbe264d10905e6db5f941b527b5a15b007198d24` for Wi-Fi. Receipts retain
their origin IDs for provenance.

`inputs.json` records the kernel patch hashes. Each kernel recipe applies its
`kernel/*.series` order, then the OS build applies `os/patches/kernel`.

`os/sources.json` pins the historical QEMU compatibility branch. That branch
carries its X2000 machine and DPU and watchdog tests as source commits.
The builder fetches from the public QEMU fork by default. Set `Q2_QEMU_REPO`
to use a different checkout path or repository URL.
The build checks out the exact commit into a dedicated source directory.
