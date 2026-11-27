#!/bin/sh
# Installed PID1. Filesystems were prepared by the minimal rescue initramfs.
loopback_setup() {
    # ifconfig assigns the same address on repeated calls without duplicate-add
    # errors. Local Tailscale proxies require this before any app/network worker.
    ifconfig lo 127.0.0.1 up
}
bootmark() {
    file=/run/q2/boot-times
    [ ! -L "$file" ] && { [ ! -e "$file" ] || { [ -f "$file" ] && [ "$(wc -c < "$file")" -lt 65408 ]; }; } || return 0
    read -r uptime idle 2>/dev/null < /proc/uptime || return 0
    case "$uptime" in *.*) ;; *) return 0 ;; esac
    case "${uptime%.*}:${uptime#*.}" in *[!0-9:]*|:*|*:) return 0 ;; esac
    printf '%s %s\n' "$uptime" "$1" >> "$file" 2>/dev/null || :
}
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
hostname q2-mainline
umask 077
mkdir -p /run/log /run/q2 /run/q2nd /mnt/mmc /state
exec </dev/ttyS3 >/dev/ttyS3 2>&1
bootmark installed-init
loopback_ready=0
if loopback_setup; then
    loopback_ready=1
else
    echo 'Loopback setup failed; player startup withheld, diagnostics retained.'
fi
bootmark loopback-complete
bootmark state-begin
/sbin/q2-state > /run/log/state 2>&1 || echo 'Internal state unavailable; diagnostics retained.'
bootmark state-complete
key=/etc/q2-bootstrap/hostkey
[ "$(cat /run/q2/state-status 2>/dev/null)" != ready ] || key=/state/ssh/hostkey
bootmark diagnostic-usb-requested
/sbin/q2-diagnostic-usb && {
    bootmark diagnostic-usb-ready
    /usr/sbin/dropbear -F -E -s -g -r "$key" -p 22 > /run/log/ssh 2>&1
} &
bootmark clock-floor-begin
/etc/init.d/S02q2-clock start
bootmark clock-floor-returned
bootmark clock-trust-begin
/etc/init.d/S25q2-clock-trust start
bootmark clock-trust-returned
bootmark clock-network-begin
[ ! -x /etc/init.d/S26q2-network-clock ] || /etc/init.d/S26q2-network-clock start
bootmark clock-network-returned
bootmark clock-services-complete
bootmark peripherals-begin
if [ "$loopback_ready" = 1 ] && /sbin/q2-player-bringup > /run/log/bringup 2>&1; then
    bootmark peripherals-complete
    if [ -x /sbin/q2-installed-services ]; then
        # Complete the serialized launcher-admission handshake before enabling
        # USB automation; a background start used to race an export's stop.
        bootmark services-admission-begin
        /sbin/q2-installed-services start > /run/log/services 2>&1 ||
            echo 'Application startup failed; diagnostics retained.'
        bootmark services-admission-returned
    else
        echo 'Application integration hook missing; diagnostics only.'
    fi
else
    echo 'Peripheral bring-up failed; diagnostics retained.'
fi
bootmark services-requested
bootmark usb-auto-start-begin
[ ! -x /usr/sbin/q2-usb-auto ] || /usr/sbin/q2-usb-auto start >> /run/log/usb-auto 2>&1
bootmark usb-auto-start-returned
echo Q2_INSTALLED_ROOT_READY
dmesg > /run/log/boot
if grep -qw q2.emulated=1 /proc/cmdline; then
    while :; do setsid sh -c 'exec sh </dev/ttyS0 >/dev/ttyS0 2>&1'; sleep 1; done
fi
while :; do sleep 60; done
