#!/bin/sh
set -eu

label=com.chusennote.monitor
plist_path="$HOME/Library/LaunchAgents/$label.plist"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
if [ -f "$plist_path" ]; then
    rm "$plist_path"
fi
printf '%s\n' "Removed $label. Log files were left intact in $HOME/Library/Logs."
