# OS specification

Each requirement has a stable ID that code, tests and documents cite. A row
states what the image, kernel or boot does and how to check it. Player
behavior is in the
[q2-player specification](https://github.com/q2-nova/q2-player/blob/main/docs/specification.md).
`tools/check_docs.py` checks that every ID is defined once and that every
cited ID exists.

## Boot

| ID | Requirement | Acceptance |
| --- | --- | --- |
| BOOT-001 | Boot is fast and reliable, including Wi-Fi from a cold start. The Wi-Fi supplies are declared: ALDO1 always on, BLDO1 and BLDO2 through the `shanling,q2-wifi-pwrseq` sequence, and the WLAN enable behind the WLAN/BT enable with a 100 ms off-on delay, so the SDIO bus is not scanned before the radio has power. Root init starts Wi-Fi early when autostart is set. | `os/test_wifi_cold_boot.py` passes, and a cold boot on the device associates with the network with the player and card healthy. |
| BOOT-002 | Root init, peripheral bring-up and the player append uptime-stamped stage marks to `/run/q2/boot-times`. The image reads ahead the file ranges the installed player needs for Home and first Play, bound to that player's exact digest. The targets are a usable player 2 s and a usable network 10 s after the kernel starts. | A boot records every stage in `/run/q2/boot-times`, and read-ahead is skipped for a player whose digest does not match. |
| BOOT-003 | An image appears as early as practical: bring-up loads the display drivers first and runs `/sbin/q2-init-splash` before audio, state, network and the player, and the player's first frame is its own startup image, separate from usable readiness. A Power wake from retained sleep shows a usable frame within 200 ms. | The splash and the player's startup frame appear before the player is usable, and the Power wake to a usable frame measures under 200 ms on the device. |

## Power and charging

| ID | Requirement | Acceptance |
| --- | --- | --- |
| POWER-001 | Cell charging can be inhibited and restored by hand for USB power measurements, through `charge_behaviour` (`auto` or `inhibit`) or the generation-checked `charge_policy_control`, with the existing limits and protections kept. `auto` is refused while the charger reports a fault. A manual write revokes any earlier policy authority. System sleep refuses to return if `charge_behaviour` changed during it. | Inhibit, read back and restore charging on the device with the charge limits unchanged, and a sleep with a changed `charge_behaviour` reports failure. |
| POWER-002 | The Q2 suspends to RAM after `[power] sleep_minutes` of idle with the screen off and nothing playing (default 10; Settings → System → Sleep When Idle offers 5, 10, 30 or 60 minutes or Never), and on Sleep from the Power menu or a held Play/Pause. Paused audio buffers survive sleep, the Power key wakes it, and a refused or unverified sleep is reported as a failure. Both panel supplies (ALDO2 and ALDO4) are off in sleep; lower sleep current takes precedence over faster wake. | An idle sleep and a Power wake return to the same player with paused audio intact, and the sleep helper reports a refused sleep as failed. |
| POWER-003 | Power Off stops USB automation and services, checks storage ownership, unmounts the card, state and user data, records the shutdown in `/state/power-history`, then asks the kernel to power off. It refuses while the card is lent to a USB host. | After Power Off and a fresh boot, the card and state mount clean and the spoken position is kept; Power Off during USB Drive Mode is refused. |
| POWER-004 | The kernel exposes the constant charge current and inhibit controls under compare-and-set policy ownership, charger fault history, PMIC die temperature and a charge monitor that reports `battery_temperature_known=0`; PMIC or TS readings are never labelled battery temperature. Policies stay independent of USB Drive Mode and keep manual inhibition (POWER-001). There is no charge ceiling control and no user charge setting. | A policy write without the current generation is refused, and a fault appears in the fault history. |

## USB

| ID | Requirement | Acceptance |
| --- | --- | --- |
| USB-001 | When a computer configures the USB gadget with VBUS present, `q2-usb-auto` lends the card once per physical attachment; a charger alone does nothing. `q2-usb-status` shows "USB Drive Mode: Edit files on your computer; Eject or unplug to return". The card returns to the player after the computer ejects it, or after an unplug confirmed by three VBUS readings of 0 taken 100 ms apart; gadget state changes never count as an unplug. Settings → Connections → USB Drive lends the card by hand. | Plugging into a computer enters USB Drive Mode once, a charger does not, and eject or unplug returns the player. |

## Network

| ID | Requirement | Acceptance |
| --- | --- | --- |
| NETWORK-002 | The image ships exactly one Tailscale daemon: `--tailscale rs` (default, tsrsd from `--tsrsd`) or `--tailscale go` (`tailscaled` from `--tailscale-dir`). The player runs whichever daemon the image has and offers no choice. | A build given both `--tsrsd` and `--tailscale-dir` is refused, and the default build contains tsrsd only. |
| WIFI-006 | Wi-Fi supports WPA2, WPA2/WPA3 transition and WPA3 with stock parity. `[[wifi.networks]] security` is `wpa2` (default), `wpa2-wpa3` (SAE and WPA-PSK, PMF optional) or `wpa3` (SAE, PMF required), with `sae_pwe=2` where SAE applies. A WPA3 network without a password is refused, never joined open. The image's wpa_supplicant build enables SAE, PMF and the PMKSA cache. | The device joins a WPA2, a transition and a WPA3 access point, and a WPA3 network without a password is refused. |

## Updates

| ID | Requirement | Acceptance |
| --- | --- | --- |
| UPDATE-001 | The player updates without replacing the OS. `q2-app-update` stages, applies, confirms, rolls back or disables a read-only SquashFS app image of at most 8 MiB, bound to the exact `/etc/q2-os-release.json`, with 2 MiB of free internal state required. An unconfirmed or damaged selection falls back to the confirmed image or to the built-in `/usr/bin/q2nd`. [App update protocol](app-update-protocol.md) gives the format. | An interrupted or unconfirmed trial starts the confirmed image or the built-in player at the next start. |

## Platform

| ID | Requirement | Acceptance |
| --- | --- | --- |
| SDHCI-001 | Both X2000 MSC controllers, MSC1 (Wi-Fi SDIO) and MSC2 (microSD), run in SDHCI v4 mode at up to 50 MHz, with the stock probe order: `mmc0` is MSC2 and `mmc1` is MSC1. | Both controllers probe in v4 mode, and the card and the radio pass I/O after boot and after sleep. |
| DISPLAY-002 | Brightness has 100 distinct lit levels: each value of `[display] brightness` (1 to 100) maps to its own PWM level from 16/255 to 255/255. | Stepping brightness from 1 to 100 gives 100 different PWM duty values. |
| DISPLAY-003 | At 100 % brightness the PWM stays in PWM mode with one low clock per period, so the backlight neither goes dark nor blinks. | At full brightness the backlight stays lit through screen off and wake cycles. |
| BUILD-001 | The production kernel builds without LTO (`CONFIG_LTO_NONE=y`). | The resolved production `.config` contains `CONFIG_LTO_NONE=y`. |

## Not implemented

| ID | Requirement | Acceptance |
| --- | --- | --- |
| USB-AUDIO-001 | The Q2 plays to an external USB DAC and works as a USB audio device for a computer. The kernel builds the USB port as a peripheral only, with no host role and no UAC gadget function. | A USB DAC enumerates and plays through the player's USB route, and a computer sees the Q2 as an audio device. |

## Requirements in other repositories

These IDs share a family with OS IDs or are cited here.

| ID | Repository |
| --- | --- |
| DISPLAY-001 | q2-player |
| DISPLAY-004 | q2-player |
| NETWORK-001 | q2-player |
| NETWORK-003 | q2-player |
| POWER-005 | q2-player |
| POWER-006 | q2-player |
| POWER-007 | q2-player |
| POWER-008 | q2-player |
| POWER-009 | q2-player |
| TEXT-002 | q2-player |
| UPDATE-002 | q2-player |
| USB-AUDIO-002 | q2-player |
| SDHCI-002 | q2-factory `projects/kernel` |
| WIFI-003 | q2-factory `projects/kernel` |
| WIFI-004 | q2-factory `projects/kernel` |
| WIFI-005 | q2-factory `projects/kernel` |
