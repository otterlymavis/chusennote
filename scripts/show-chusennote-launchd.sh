#!/bin/sh
set -eu

label=com.chusennote.monitor
launchctl print "gui/$(id -u)/$label"
printf '\nRecent output:\n'
tail -n 20 "$HOME/Library/Logs/chusennote-monitor.log" 2>/dev/null || true
tail -n 20 "$HOME/Library/Logs/chusennote-monitor-error.log" 2>/dev/null || true
