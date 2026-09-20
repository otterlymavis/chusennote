#!/bin/sh
set -eu

usage() {
    printf '%s\n' "Usage: $0 [--kind event|artist] [--interval-minutes N] [--db PATH] [--python PATH] [--env-file PATH] [--dry-run]"
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
kind=event
interval_minutes=60
db_path="$repo_root/chusennote.sqlite3"
python_command=python3
env_file=
dry_run=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --kind) kind=${2-}; shift 2 ;;
        --interval-minutes) interval_minutes=${2-}; shift 2 ;;
        --db) db_path=${2-}; shift 2 ;;
        --python) python_command=${2-}; shift 2 ;;
        --env-file) env_file=${2-}; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

case "$kind" in event|artist) ;; *) printf '%s\n' "Kind must be event or artist." >&2; exit 2 ;; esac
case "$interval_minutes" in ''|*[!0-9]*) printf '%s\n' "Interval must be a positive integer." >&2; exit 2 ;; esac
if [ "$interval_minutes" -lt 1 ]; then
    printf '%s\n' "Interval must be at least 1 minute." >&2
    exit 2
fi

case "$db_path" in /*) ;; *) db_path="$repo_root/$db_path" ;; esac
if [ -n "$env_file" ]; then
    case "$env_file" in /*) ;; *) env_file="$repo_root/$env_file" ;; esac
    if [ ! -f "$env_file" ]; then
        printf '%s\n' "Environment file not found: $env_file" >&2
        exit 2
    fi
fi

case "$python_command" in
    /*) python_path=$python_command ;;
    *)
        python_path=$(command -v "$python_command" 2>/dev/null || true)
        if [ -z "$python_path" ]; then
            printf '%s\n' "Python executable not found: $python_command" >&2
            exit 2
        fi
        ;;
esac
if [ ! -x "$python_path" ]; then
    printf '%s\n' "Python executable is not runnable: $python_path" >&2
    exit 2
fi

config_root=${XDG_CONFIG_HOME:-"$HOME/.config"}
unit_dir="$config_root/systemd/user"
service_path="$unit_dir/chusennote-monitor.service"
timer_path="$unit_dir/chusennote-monitor.timer"
db_dir=$(dirname -- "$db_path")
if [ "$dry_run" -eq 0 ] && [ ! -d "$db_dir" ]; then
    printf '%s\n' "Database directory does not exist: $db_dir" >&2
    exit 2
fi

unit_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

escaped_repo=$(unit_escape "$repo_root")
escaped_python=$(unit_escape "$python_path")
escaped_db=$(unit_escape "$db_path")
escaped_db_dir=$(unit_escape "$db_dir")
environment_line=
if [ -n "$env_file" ]; then
    environment_line="EnvironmentFile=-\"$(unit_escape "$env_file")\""
fi

service_content="[Unit]
Description=chusennote $kind watch check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=\"$escaped_repo\"
$environment_line
ExecStart=\"$escaped_python\" \"$escaped_repo/lottery_monitor.py\" $kind run --db \"$escaped_db\"
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=\"$escaped_db_dir\"
"

timer_content="[Unit]
Description=Run chusennote every $interval_minutes minute(s)

[Timer]
OnBootSec=2min
OnUnitActiveSec=${interval_minutes}min
Persistent=true
Unit=chusennote-monitor.service

[Install]
WantedBy=timers.target
"

if [ "$dry_run" -eq 1 ]; then
    printf '%s\n' "# $service_path" "$service_content" "# $timer_path" "$timer_content"
    exit 0
fi

if ! command -v systemctl >/dev/null 2>&1; then
    printf '%s\n' "systemctl is required to install the Linux user timer." >&2
    exit 2
fi

mkdir -p "$unit_dir"
umask 077
printf '%s\n' "$service_content" > "$service_path"
printf '%s\n' "$timer_content" > "$timer_path"
systemctl --user daemon-reload
systemctl --user enable --now chusennote-monitor.timer
printf '%s\n' "Installed and started chusennote-monitor.timer" "Database: $db_path" "Kind: $kind" "Interval: $interval_minutes minute(s)"
