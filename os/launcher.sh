#!/bin/sh
# The player launcher, installed as /usr/libexec/q2-launcher by
# os/post-build.py when the image has a player. Runs Q2ND_APP/player,
# restarts it on request, and trials a staged replacement (player.next with
# player.next.sha256, from q2-app-update): the previous binary is kept, the
# candidate is confirmed after five ready (or, with Q2ND_REQUIRE_HEALTH=1,
# healthy) samples, and rolled back on failure or after a power loss during
# the trial. It exits 81 when the player does, after three non-zero player
# exits in a row, or when Q2ND_APP/player is missing.
set -u
APP=${Q2ND_APP:-/mnt/mmc/.q2nd}
RUN=${Q2ND_RUNTIME:-/run/q2nd}
export Q2ND_APP="$APP" Q2ND_RUNTIME="$RUN"
umask 077
mkdir -p "$RUN"
chmod 700 "$RUN"
cd "$APP" || exit 81
player_pid=
ssh_pid=
cleanup() {
    [ -z "$player_pid" ] || kill -TERM "$player_pid" 2>/dev/null
    [ -z "$ssh_pid" ] || kill -TERM "$ssh_pid" 2>/dev/null
    [ -z "$ssh_pid" ] || wait "$ssh_pid" 2>/dev/null
    rm -f "$RUN/player.pid" "$RUN/ready" "$RUN/restart" "$RUN/launcher.pid" "$RUN/launcher.wake"
}
trap cleanup EXIT
trap 'exit 81' TERM INT HUP
# After a confirmed start the launcher blocks in `wait`, not a polling loop
# (a busybox `sleep` per second for days). q2-app-update sends USR1 after
# touching restart; launcher.wake says this launcher wakes on it.
woken=0
trap 'woken=1' USR1
echo $$ > "$RUN/launcher.pid"
echo usr1 > "$RUN/launcher.wake"
if [ -x "$APP/dropbear" ] && [ -f "$APP/ssh.sh" ]; then
    sh "$APP/ssh.sh" >> "$RUN/ssh.log" 2>&1 &
    ssh_pid=$!
fi
failures=0
trial=0
while [ -f "$APP/player" ]; do
    # Recovery after a loss of power during an unconfirmed update.
    if [ -f "$APP/player.trial" ] && [ -f "$APP/player.previous" ]; then
        cp "$APP/player.previous" "$APP/player.restore" && mv "$APP/player.restore" "$APP/player" || exit 81
        rm -f "$APP/player.trial"
        sync
    fi
    if [ -f "$APP/player.next" ] && [ -f "$APP/player.next.sha256" ]; then
        if sha256sum -c "$APP/player.next.sha256" >/dev/null 2>&1; then
            cp "$APP/player" "$APP/player.previous.new" && mv "$APP/player.previous.new" "$APP/player.previous" || exit 81
            # Persist rollback before changing the executable.
            : > "$APP/player.trial"
            sync
            mv "$APP/player.next" "$APP/player" || exit 81
            chmod +x "$APP/player"
            sync
            trial=1
        else
            echo 'SSH update rejected: SHA-256 mismatch'
        fi
        rm -f "$APP/player.next.sha256"
    fi
    rm -f "$RUN/ready" "$RUN/healthy" "$RUN/restart"
    "$APP/player" &
    player_pid=$!
    echo "$player_pid" > "$RUN/player.pid"
    seconds=0
    healthy=0
    restart=0
    code=
    while kill -0 "$player_pid" 2>/dev/null; do
        if [ -f "$RUN/restart" ]; then
            restart=1
            kill -TERM "$player_pid" 2>/dev/null
            # Graceful shutdown saves the queue and releases the DAC.
            n=0
            while kill -0 "$player_pid" 2>/dev/null && [ "$n" -lt 10 ]; do sleep 1; n=$((n + 1)); done
            kill -KILL "$player_pid" 2>/dev/null || :
            break
        fi
        if [ "$trial" -eq 1 ]; then
            ready_file="$RUN/ready"
            [ "${Q2ND_REQUIRE_HEALTH:-0}" != 1 ] || ready_file="$RUN/healthy"
            if [ "$(cat "$ready_file" 2>/dev/null)" = "$player_pid" ]; then healthy=$((healthy + 1)); else healthy=0; fi
            if [ "$healthy" -ge 5 ]; then
                rm -f "$APP/player.trial"
                sync
                trial=0
                echo 'SSH update confirmed: UI ready'
            elif [ "$seconds" -ge 30 ]; then
                echo 'SSH update failed: UI startup timed out'
                kill -KILL "$player_pid" 2>/dev/null || :
                break
            fi
        fi
        if [ "$trial" -eq 1 ]; then
            sleep 1
            seconds=$((seconds + 1))
            continue
        fi
        # Returns when the player exits, or early for a trapped USR1.
        woken=0
        wait "$player_pid"
        status=$?
        if [ "$woken" -eq 0 ]; then code=$status; break; fi
    done
    if [ -z "$code" ]; then
        wait "$player_pid"
        code=$?
    fi
    player_pid=
    rm -f "$RUN/player.pid" "$RUN/ready"
    if [ "$trial" -eq 1 ]; then
        echo 'Restoring previous player'
        # The persisted trial marker triggers restoration at the loop top.
        trial=0
        continue
    fi
    [ "$code" -eq 81 ] && exit 81
    if [ "$code" -eq 0 ] || [ "$restart" -eq 1 ]; then failures=0; else failures=$((failures + 1)); fi
    [ "$failures" -lt 3 ] || exit 81
    sleep 1
done
exit 81
