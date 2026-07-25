#!/bin/sh
set -e

mkdir -p /data/characters /data/personas /data/themes /data/thumbs

# Ensure the data directory (which may be a host-mounted volume owned by root)
# is writable by the cozy user before dropping privileges.
chown -R cozy:cozy /data

exec gosu cozy "$@"
