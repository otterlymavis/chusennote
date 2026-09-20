#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
kind=event
db_path="$repo_root/chusennote.sqlite3"
python_command=python3
env_file=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --kind) kind=${2-}; shift 2 ;;
        --db) db_path=${2-}; shift 2 ;;
        --python) python_command=${2-}; shift 2 ;;
        --env-file) env_file=${2-}; shift 2 ;;
        *) printf '%s\n' "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

case "$kind" in event|artist) ;; *) printf '%s\n' "Kind must be event or artist." >&2; exit 2 ;; esac
case "$db_path" in /*) ;; *) db_path="$repo_root/$db_path" ;; esac

if [ -n "$env_file" ]; then
    case "$env_file" in /*) ;; *) env_file="$repo_root/$env_file" ;; esac
    if [ ! -f "$env_file" ]; then
        printf '%s\n' "Environment file not found: $env_file" >&2
        exit 2
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(printf '%s' "$line" | tr -d '\r')
        case "$line" in ''|'#'*) continue ;; esac
        name=${line%%=*}
        value=${line#*=}
        if [ "$name" = "$line" ]; then
            printf '%s\n' "Invalid environment line (missing =): $name" >&2
            exit 2
        fi
        case "$name" in
            ''|[0-9]*|*[!A-Za-z0-9_]*)
                printf '%s\n' "Invalid environment variable name: $name" >&2
                exit 2
                ;;
        esac
        export "$name=$value"
    done < "$env_file"
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

cd "$repo_root"
exec "$python_path" lottery_monitor.py "$kind" run --db "$db_path"
