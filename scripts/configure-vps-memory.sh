#!/bin/sh
set -eu

swap_file=/swapfile
if [ ! -e "$swap_file" ]; then
    free_kb=$(df -Pk / | awk 'NR == 2 {print $4}')
    if [ "$free_kb" -lt 4194304 ]; then
        echo 'At least 4 GiB of free disk is required before creating swap' >&2
        exit 1
    fi
    fallocate -l 2G "$swap_file"
    chmod 600 "$swap_file"
    mkswap "$swap_file" >/dev/null
fi
if ! swapon --show=NAME --noheadings | grep -qx "$swap_file"; then
    swapon "$swap_file"
fi
if ! grep -q '^/swapfile none swap sw 0 0$' /etc/fstab; then
    printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
fi
printf '%s\n' 'vm.swappiness=10' > /etc/sysctl.d/99-oncomap-memory.conf
sysctl -p /etc/sysctl.d/99-oncomap-memory.conf >/dev/null
swapon --show
