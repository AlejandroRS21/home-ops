#!/bin/sh
# Home-Ops container entrypoint.
#
# If $HOME_OPS_CONFIG is unset or points at a missing file, provision a
# user profile from the shipped template into the persistent /app/data
# volume (no-overwrite, idempotent), retarget HOME_OPS_CONFIG to it, then
# exec the daemon so PID 1 stays the application process.
set -eu

if [ -z "${HOME_OPS_CONFIG:-}" ] || [ ! -f "${HOME_OPS_CONFIG}" ]; then
    mkdir -p /app/data
    if [ ! -f /app/data/user_profile.yml ]; then
        template=/app/config/user_profile.template.yml
        [ -f "$template" ] || template=/app/templates/user_profile.template.yml
        cp "$template" /app/data/user_profile.yml
    fi
    export HOME_OPS_CONFIG=/app/data/user_profile.yml
fi

exec "$@"