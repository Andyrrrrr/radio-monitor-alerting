#!/bin/sh
# Wait up to 90 s for the wifi adapter to appear at boot. See deploy/README.md.
i=0
while [ "$i" -lt 90 ]; do
    [ -e /sys/class/net/wlp1s0 ] && exit 0
    i=$((i + 1))
    sleep 1
done
echo "wlp1s0 did not appear within 90 s" >&2
exit 1
