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
interval_seconds=$((interval_minutes * 60))

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

xml_escape() {
    printf '%s' "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g; s/'"'"'/\&apos;/g'
}

runner="$repo_root/scripts/run-chusennote-once.sh"
launch_agents="$HOME/Library/LaunchAgents"
logs_dir="$HOME/Library/Logs"
plist_path="$launch_agents/com.chusennote.monitor.plist"
label=com.chusennote.monitor

env_arguments=
if [ -n "$env_file" ]; then
    env_arguments="
      <string>--env-file</string>
      <string>$(xml_escape "$env_file")</string>"
fi

plist_content="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml_escape "$runner")</string>
    <string>--kind</string><string>$kind</string>
    <string>--db</string><string>$(xml_escape "$db_path")</string>
    <string>--python</string><string>$(xml_escape "$python_path")</string>$env_arguments
  </array>
  <key>WorkingDirectory</key><string>$(xml_escape "$repo_root")</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>$interval_seconds</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$(xml_escape "$logs_dir/chusennote-monitor.log")</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$logs_dir/chusennote-monitor-error.log")</string>
</dict>
</plist>"

if [ "$dry_run" -eq 1 ]; then
    printf '%s\n' "# $plist_path" "$plist_content"
    exit 0
fi

if [ "$(uname -s)" != "Darwin" ] || ! command -v launchctl >/dev/null 2>&1 || ! command -v plutil >/dev/null 2>&1; then
    printf '%s\n' "launchctl and plutil on macOS are required to install the LaunchAgent." >&2
    exit 2
fi

mkdir -p "$launch_agents" "$logs_dir"
umask 077
printf '%s\n' "$plist_content" > "$plist_path"
plutil -lint "$plist_path"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl enable "gui/$(id -u)/$label"
launchctl bootstrap "gui/$(id -u)" "$plist_path"
printf '%s\n' "Installed and started $label" "Database: $db_path" "Kind: $kind" "Interval: $interval_minutes minute(s)"
