# System sleep

The installed image suspends to RAM with `q2-system-sleep`
([os/install/integration/sbin/q2-system-sleep](../os/install/integration/sbin/q2-system-sleep)).
The player calls it after preparing its own state; the process and its RAM
stay alive through the sleep. The X2000 enters retained-CPU SLEEP with DDR in
self-refresh. The Power key wakes it. The player's sleep controls and idle
policy are documented in q2-player.

## Helper sequence

`q2-system-sleep` takes no arguments and needs root. It takes three lock
directories in order, refusing if any is held: `/run/q2/usb-storage.lock`,
`/run/q2nd/control.lock` and `/run/q2-network/control.lock`. Then:

1. **Admission.** It refuses unless:
   - the card is local (`/run/q2/state-status` is `ready`, and any USB
     session is `local-ready`);
   - the kernel reports `version=1 normal_retained=1` in
     `/sys/kernel/q2_suspend/entry_policy` and has a readable `proof`;
   - `mem` is in `/sys/power/state`, `pm_test` is `[none]` and `mem_sleep`
     is `[deep]`;
   - the player is ready (`/run/q2nd/player.pid` and `/run/q2nd/ready`).
2. **Snapshot.** It records the boot ID, the battery `charge_behaviour`,
   the `suspend_stats` success, fail and resume-failure counters, the
   `normal_exclk` clock policy and the kernel's suspend proof line. If
   `wpa_supplicant` is running, it records its PID, start time and control
   socket.
3. **Preparation.** It stops the supervisor (`S79q2-supervisor stop`, which
   disarms the watchdog), checks storage again and syncs. It sends
   `SUSPEND` to that same `wpa_supplicant`; once sent, cleanup owes it a
   `RESUME`, and neither is ever sent to a replacement process. With
   `Q2_RETAIN_DRM_FOR_PM=1` it arms the display driver's asynchronous
   restore (`/sys/class/drm/card0/device/q2_async_resume`).
4. **Suspend.** It writes the current `/sys/power/wakeup_count` back, so a
   wake event that raced preparation makes the suspend fail, writes
   `suspending` to `/run/q2/sleep-status` and writes `mem` to
   `/sys/power/state`. With `Q2_NOTIFY_KERNEL_RETURN=1` it prints
   `Q2_KERNEL_RETURN` as soon as the write returns.
5. **Verification.** It sends `RESUME` to the supplicant and refuses the
   return unless: the boot ID is unchanged; the success count rose and the
   fail and resume-failure counts did not; the clock policy is unchanged;
   the card is still local; the charge behaviour is unchanged; and the
   proof line shows one more attempt and one more return, `valid=1`,
   `stage=7`, `error=0`, DDR self-refresh in the entry DDRC and PHY words,
   no diagnostic round trip or CPU-reset return, the retained SLEEP clock and
   reset state in OPCR and LCR, and an RTC exit time not before entry.
6. **Cleanup** (on every exit): resume the supplicant if owed, restart the
   supervisor, wait for the display restore to report `ready`, write
   `resumed` or `failed` to `/run/q2/sleep-status`, and remove the locks.

A failed or ambiguous step is a failure; a dark screen is not evidence of
sleep. No RTC alarm, periodic reboot or forced reconnect is part of normal
sleep. Alarms are used only as test recovery.

Expected OPCR bits (mask `0xc470001c`): `0x40700014` when the kernel keeps
EXCLK in sleep (`normal_exclk` `version=1 retain=1`, or a kernel without the
attribute), `0x40700004` with `retain=0`.

Linux freezes tasks and suspends devices; the kernel's X2000 suspend path is
in q2-nova/linux `arch/mips/generic/pm-ingenic-x2000.c`. The separate
CPU-off route, with a CPU reset and context restore, has an unresolved reset
fault and is not used.

## Wake

| Fact | Source | Level |
| --- | --- | --- |
| A Power-key press wakes the Q2 from SLEEP through IRQ 31: its count goes from 0 to 1 and `pm_wakeup_irq` names it. An RTC-only return or an unrelated interrupt does not show a Power wake | IRQ counts and `pm_wakeup_irq` across a sleep with an RTC fallback armed | device-verified |
| From helper return to the player's first presented frame took 859.955 ms in one Power wake; this is not press-to-light latency | helper and player timestamps | device-verified |

## Power in sleep

| Fact | Source | Level |
| --- | --- | --- |
| Switching the microSD pins to GPIO inputs without pulls in SLEEP saves 34 to 36 mW at the USB input | [hardware.md](hardware.md#msc-sd-and-sdio) | device-verified |
| A 13.206 s window of one installed sleep measured 79.137 mW at the USB input; the whole trace failed the sample checks | USB-input meter, uncalibrated | device-verified |
| Rail-control registers do not change across suspend | [hardware.md](hardware.md#pmic-x-powers-axp2101) | device-verified |

Digital reset, clock and supply commands are not measurements of a rail
being off.

## Tests and acceptance

`python3 os/test_system_sleep.py` runs the helper against generated sysfs,
proc and supervisor fixtures: admission refusals, the proof checks, the
supplicant notification pairing and every unwind path.

On a Q2, 20 of 20 RTC-woken cycles passed with USB attached: same boot and
player, Wi-Fi and gateway back, silent `aplay`, card reads with caches
dropped, marker writes and charging checks. That series is the acceptance
test after a change to kernel suspend, this helper, the player's sleep and
wake, the PMIC or wake handling, before the release that carries it. It
does not cover sleep on battery, USB or PMIC wake sources, or resume with
media paused or playing.
