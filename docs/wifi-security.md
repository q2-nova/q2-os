# Wi-Fi security

The image's `wpa_supplicant` supports WPA2 personal, WPA2/WPA3 transition and
WPA3 personal (SAE). The player writes the network profiles; this page covers
the supplicant build and the profiles it accepts. Network time is in
[network-time.md](network-time.md).

## Supplicant builds

`os/hardware/build-wifi-tools.py` cross-builds `wpa_supplicant` and `wpa_cli`
2.12 with libnl 3.12 against the SDK, from pinned source digests:

| Profile | Command | Crypto | Key management |
| --- | --- | --- | --- |
| WPA2 | `build-wifi-tools.py --buildroot-output OUT` | internal (libtommath) | WPA-PSK |
| WPA3 | `build-wifi-tools.py --buildroot-output OUT --wpa3 --output FRESH_DIR` | OpenSSL 3.6.5, EC only, linked statically | WPA-PSK, SAE, PMF |

Both use the nl80211 driver and a control socket, and have no bgscan,
autoscan or P2P. The WPA3 profile keeps the SDK's MIPS32r2/FP64 ABI and adds
no dynamic TLS or atomics library; it needs a fresh output directory.
`artifact.json` in the output records the profile, source digests and binary
digests. `os/install/build.py --wifi-tools DIR` installs the pair into an
installed image and is required for hardware builds.

The WPA3 configuration sets `CONFIG_SAE`, `CONFIG_IEEE80211W` and
`CONFIG_IEEE8021X_EAPOL`. Upstream 2.12 builds the real PMKSA cache only with
`CONFIG_IEEE8021X_EAPOL`; with SAE and PMF alone the cache functions are
empty. The RTL8733BS driver starts station association from `SET_PMKSA`
after external authentication succeeds, so without the cache an SAE
association never starts. The driver's external-authentication fields are
normalised in [kernel/rtl8733bs](../kernel/rtl8733bs/README.md) (patch 0004).

## Profiles

`[[wifi.networks]]` in the player's `config.toml` takes
`security = "wpa2"`, `"wpa2-wpa3"` or `"wpa3"`; omitted means `wpa2`. The
player sets these supplicant fields:

| `security` | `key_mgmt` | `ieee80211w` | `sae_pwe` |
| --- | --- | --- | --- |
| `wpa2` | `WPA-PSK` | 0 | — |
| `wpa2-wpa3` | `SAE WPA-PSK` | 1 (optional) | 2 |
| `wpa3` | `SAE` | 2 (required) | 2 |

`sae_pwe=2` accepts both hunting-and-pecking and hash-to-element. A WPA3
network without a password is refused rather than made open, and a rejected
security change leaves the old profile disabled. The image owns
`/state/network/wpa.conf`; it does not import the stock firmware's
configuration. The stock supplicant (2.9) is not expected to accept a 2.12
hash-to-element profile.

## Checks

| Check | What it shows | Level |
| --- | --- | --- |
| `python3 os/test_wifi_security.py --supplicant PATH` | a native supplicant on the `none` driver accepts, saves and reloads the three profiles with the expected `key_mgmt` and `ieee80211w` | host |
| `os/hardware/prepare-wpa3-qualification.py` | prepares generated WPA2, transition and SAE-only access points and a matching target overlay from a verified WPA3 tools build | host (preparation only) |
| SAE-only, transition and WPA2 networks on a generated AP: wrong-password refusal, negotiated key management and PMF, DHCP, payload hash, sustained transfer and return to normal service | association and transfer on a Q2 | device-verified |

Not covered: reconnection after an access-point restart, long-run
reconnection reliability, energy, cold-power behaviour and a newer driver.
NTS is not available: the WPA3 OpenSSL build does not provide chrony's NTS
backend.
