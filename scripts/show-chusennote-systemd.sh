#!/bin/sh
set -eu

systemctl --user status chusennote-monitor.timer --no-pager
printf '\n'
systemctl --user list-timers chusennote-monitor.timer --no-pager
printf '\nRecent service output:\n'
journalctl --user -u chusennote-monitor.service -n 20 --no-pager
