# Q2 stock radio source and relink materials

This archive accompanies a stock Q2 firmware build. `radio-source.json`
identifies the SDK and platform used, the shipped Wi-Fi binaries, and the
production Linux revision, kernel build environment and Realtek module. The
upstream archives in `sources/` contain the original license notices, including
the libnl LGPL text. The radio tools use libnl, wpa_supplicant, hostapd, iw and
dnsmasq. The archive also includes the signed wireless regulatory database's
upstream source and ISC notice.

`sources/rtl8733bs-patched.tar.gz` contains the complete patched vendor
driver tree. It includes Realtek's GPLv2 notice and the controller firmware
arrays in the form supplied in `hal/rtl8733b/hal8733b_fw.c`. The companion
`rtl8733bs/` directory has the selected downstream patches, preparation
recipe and matching Linux configuration. Use the production Linux and
corresponding OS sources in the selected q2-os release and the
`os/install/kernel_build.py` recipe to build the driver and kernel. The Realtek
arrays are the vendor's published form of the controller firmware.

## Relink with a modified libnl

Use the relocated SDK from the q2-os component release named in the stock
firmware receipt. Build the included LGPL libnl sources with that SDK and
`--disable-shared --enable-static`, as shown in the included Wi-Fi build recipe.
Point the relinker at the directory with the resulting `libnl-3.a` and
`libnl-genl-3.a` archives.

```sh
python3 relink.py --sdk /path/to/relocated-sdk \
  --libnl /path/to/modified/libnl/install/lib --out relinked
```

The archive supplies the exact non-libnl objects and link commands for
`wpa_supplicant`, `hostapd` and `iw`. Passing the supplied `libnl/` directory
instead reproduces the shipped binaries, whose SHA-256 digests are in
`radio-source.json`.
