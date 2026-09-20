#!/bin/sh
set -eu

config_root=${XDG_CONFIG_HOME:-"$HOME/.config"}
unit_dir="$config_root/systemd/user"
service_path="$unit_dir/chusennote-monitor.service"
timer_path="$unit_dir/chusennote-monitor.timer"

systemctl --user disable --now chusennote-monitor.timer 2>/dev/null || true
if [ -f "$service_path" ]; then
    rm "$service_path"
fi
if [ -f "$timer_path" ]; then
    rm "$timer_path"
fi
systemctl --user daemon-reload
systemctl --user reset-failed chusennote-monitor.service 2>/dev/null || true
printf '%s\n' "Removed chusennote user systemd units. Journal history was left intact."
