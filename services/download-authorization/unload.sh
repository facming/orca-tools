#!/bin/sh
# Explicit administrator action; retained state is never deleted.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Administrator required' >&2; exit 1; }
systemctl stop orca-download-sshd.service
systemctl stop orca-download-authority.service
printf '%s\n' 'Entry point and authority stopped. All accounts, state, tombstones, keys and exports retained for manual audit.'
