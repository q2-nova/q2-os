# Network time and RTC trust

The image sets its clock by basic, unauthenticated NTP from chrony and keeps
the RTC in step with it. NTS is not provided. The player's HTTPS
certificate checks stay normal; `/run/q2/clock-status` tells it how far the
clock can be trusted.

## Clock status

`/run/q2/clock-status` holds one word:

| Value | Set by | Meaning |
| --- | --- | --- |
| `build-floor-unsynchronized` | `S02q2-clock` at boot | the RTC read earlier than the image's build time (`/etc/q2-build-epoch`); the system clock is set to that floor, which is not written to the RTC |
| `rtc-or-system` | `S02q2-clock` | the RTC is at or after the build time; its source is not known |
| `network-ntp-rtc` | `S25q2-clock-trust` | `rtc-or-system`, the RTC reads, and `/state/trust/network-ntp-rtc` exists: an earlier boot synchronized by NTP and wrote the RTC |
| `trusted-rtc` | `S25q2-clock-trust` | `rtc-or-system`, the RTC reads, and the clock is at or after a locally provisioned `/state/trust/clock-epoch` |
| `network-ntp` | the time service | chrony reports synchronization in this boot |

A reset RTC stays unsynchronized until network time arrives. There is no
accepted-time window, `maxchange` rule or relaxed certificate check.

## Service

`os/network-time/build.py` builds chrony 4.8 (pinned digest) with the SDK and
produces the binaries and an overlay. `/etc/q2-network/chrony.conf`:

- `server time.cloudflare.com iburst`: Cloudflare's standard UTC service. A
  pool source must follow the [NTP Pool vendor policy](https://www.ntppool.org/en/vendors.html)
  and must not be mixed with leap-smearing servers.
- `makestep 1.0 3`, chrony's standard start-up step.
- `cmdport 0`, `port 0`: no IP command or server port; control is a Unix
  socket in `/run/q2-time`.
- `rtcsync`; logs go to `/run/log`. No measurement, drift or log file is
  written to NAND.

`/usr/libexec/q2-network/time start|stop|online` runs the service:

- `start` starts one watcher (refusing a second through a lock directory),
  which runs `chronyd` in the foreground and polls `chronyc waitsync`: every
  10 s until synchronized, then every 60 s until the RTC is written.
- On synchronization it writes `network-ntp` to the clock status, writes the
  RTC (`hwclock -w -u`) and reads it back. With state mounted
  (`/run/q2/state-status` is `ready`), the first success creates
  `/state/trust/network-ntp-rtc` (`time.cloudflare.com basic-ntp`), a source
  marker with no stored epoch. After that the watcher waits on chronyd.
- `online` tells chronyd to retry at once when DHCP supplies a route.
- `stop` signals only a PID whose command line is the watcher, waits up to
  10 s, and the watcher stops chronyd before it exits.

Installed root init runs `S26q2-network-clock start` when it is present, after
`S02q2-clock` and `S25q2-clock-trust`. The service does not depend on card
ownership or USB export. With no network the player and SSH still start, and
an unsynchronized clock does not pass the HTTPS trust gate.

## NTS

chrony's NTS backend needs GnuTLS and Nettle, which the SDK lacks; their MIPS
cost is not measured. The player's Rust TLS and the WPA3 OpenSSL build do not
provide that backend. Certificate bootstrap without trusted time is unsolved.
References: [chrony FAQ](https://chrony-project.org/faq.html),
[Cloudflare NTS](https://developers.cloudflare.com/time-services/nts/).

## Checks

| Check | What it shows | Level |
| --- | --- | --- |
| `python3 os/network-time/test_service.py` | source marker without an epoch, failed RTC write not recorded as persistent, no marker without state, no rewrite after reboot, no signal to a foreign PID, the configuration, and an owned shutdown | host |
| `python3 os/network-time/test_client.py --native-bin-dir DIR` | a native chrony build reaches `waitsync` against a synthetic NTP server with `-x` (no host clock change) | host |
| One-shot acquisition on a Q2 corrects the clock and the RTC reads back the same UTC time | RAM image, generated network, no NAND state writes | device-verified |
| The installed service synchronizes after Wi-Fi reconnects | installed image | device-verified |

Not checked on a Q2: acquisition from a reset RTC, start-up and status with no
network, HTTPS after synchronization with normal certificate checks, and RTC
retention through a full power removal and the next boot.
