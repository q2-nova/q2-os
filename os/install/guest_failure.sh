#!/bin/sh
# Fixed public shutdown evidence, only in the disposable installation guest.
set -efu
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
[ ! -L /state/.install-fixture ] && [ -f /state/.install-fixture ] || exit 2
grep -Eq '^install-boot-[0-9a-f]+$' /state/.install-fixture || exit 2
echo Q2_GUEST_FAILURE_BEGIN
for file in /run/q2/boot-times /run/q2nd/service-boot-times /run/q2nd/boot-times; do
    [ ! -L "$file" ] && [ -f "$file" ] || continue
    [ "$(wc -c < "$file")" -le 262144 ] || continue
    printf 'stages=%s\n' "$file"
    awk 'NF==2 && $1 ~ /^[0-9]+[.][0-9]+$/ && ($2 ~ /^module-[a-zA-Z0-9_-]+-(begin|complete)$/ || $2 ~ /^(installed-init|peripherals-begin|peripherals-complete|service-stop-supervisor-begin|service-stop-supervisor-returned|service-stop-launcher-begin|service-stop-launcher-returned|service-cleanup-begin|service-cleanup-returned|service-cleanup-supervisor-begin|service-cleanup-supervisor-returned|service-cleanup-child-begin|service-cleanup-child-returned|media-setup-begin|media-setup-complete|player-launch-requested|main-entry|app-start|first-frame|shutdown-begin|shutdown-settings-saved|shutdown-network-stopped|shutdown-player-stopped|shutdown-library-saved|shutdown-platform-close-begin|shutdown-platform-close-returned|shutdown-exit-requested)$/) {if (++count<=128) print $1,$2}' "$file"
done
# This branch is already gated by the disposable fixture marker above.
# Never deploy/use it to collect real owner bringup or application logs.
fixture_scratch=/run/q2-guest-failure-$$
umask 077
mkdir "$fixture_scratch" || exit 3
fixture_cleanup() {
    rm -f "$fixture_scratch/profile" "$fixture_scratch/progress" "$fixture_scratch/bringup"
    rmdir "$fixture_scratch"
}
trap fixture_cleanup EXIT
fixture_file() {
    fixture_path=$1; fixture_cap=$2; fixture_label=$3; fixture_parent=${1%/*}
    if [ -L "$fixture_parent" ] || [ ! -d "$fixture_parent" ] || [ -L "$fixture_path" ] || [ ! -f "$fixture_path" ]; then
        printf 'generated_file=%s status=unavailable\n' "$fixture_label"; return 0
    fi
    fixture_snapshot=$fixture_scratch/$fixture_label
    (set -C; head -c "$((fixture_cap+1))" "$fixture_path" > "$fixture_snapshot") || return 1
    fixture_bytes=$(wc -c < "$fixture_snapshot")
    printf 'Q2_GENERATED_FILE_BEGIN %s bytes=%s limit=%s\n' "$fixture_label" "$fixture_bytes" "$fixture_cap"
    head -c "$fixture_cap" "$fixture_snapshot"
    printf '\nQ2_GENERATED_FILE_END %s\n' "$fixture_label"
}
fixture_file /run/q2-console-profile/result 1024 profile
fixture_file /run/q2-console-profile/progress 32 progress
fixture_file /run/log/bringup 2048 bringup
for role in launcher player supervisor; do
    file=/run/q2nd/$role.pid
    [ ! -L "$file" ] && [ -f "$file" ] || continue
    [ "$(wc -c < "$file")" -le 20 ] || continue
    pid=; IFS= read -r pid < "$file" || :
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    [ "$pid" -gt 1 ] || continue
    printf 'owner=%s pid=%s\n' "$role" "$pid"
    count=0
    # Globbing is needed only for this fixed proc directory enumeration.
    set +f
    for task in /proc/"$pid"/task/[0-9]*; do
        set -f
        [ -d "$task" ] || continue
        count=$((count+1)); [ "$count" -le 64 ] || { echo tasks_truncated=1; break; }
        tid=${task##*/}
        # Strip the parenthesized comm without exporting it or argv/environment.
        stat=; { IFS= read -r stat < "$task/stat"; } 2>/dev/null || :
        rest=${stat##*) }
        # Kernel stat fields after comm are scalar words. No subprocess per task.
        set -- $rest
        if [ "$#" -ge 20 ]; then
            state=$1; parent=$2; shift 19; start=$1
            case "$state" in R|S|D|Z|T|W|X|I|P|t)
                case "$parent$start" in ''|*[!0-9]*) ;; *) printf 'tid=%s state=%s ppid=%s start_ticks=%s\n' "$tid" "$state" "$parent" "$start" ;; esac
            esac
        fi
        channel=; { IFS= read -r channel < "$task/wchan"; } 2>/dev/null || :
        case "$channel" in ''|*[!a-zA-Z0-9_]*) ;; *) printf 'tid=%s wchan=%.80s\n' "$tid" "$channel" ;; esac
    done
done
echo Q2_GUEST_FAILURE_END
