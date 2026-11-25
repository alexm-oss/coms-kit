#!/usr/bin/env bash
# Sample one-minute load and, when available, operating system thermal data.
set -euo pipefail
export LC_ALL=C

usage() {
  cat <<'EOF'
Usage: load-watch.sh [--once] [--samples FILE]

Poll one-minute system load and thermal data until strain lasts STRAIN_MINUTES.
--once prints the current sample and verdict. --samples uses synthetic rows
with a load value and a thermal value (- if unavailable), without sleeping.

Environment:
  LOAD_LIMIT      one-minute load above this is strain (default 16)
  THERMAL_LIMIT   strain at or above this threshold
                  (macOS pressure level, default 2; Linux degrees C, default 85)
  STRAIN_MINUTES  sustained minutes before printing and exiting (default 10)
  INTERVAL        seconds between samples, integer at least 1 (default 60)
EOF
}

once=0
samples=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --once) once=1; shift ;;
    --samples)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        printf '%s\n' 'load-watch.sh: --samples requires a file' >&2
        exit 2
      fi
      samples=$2
      shift 2
      ;;
    *) printf '%s\n' 'load-watch.sh: invalid option (see --help)' >&2; exit 2 ;;
  esac
done

os=$(uname -s)
case "$os" in
  Darwin) default_thermal=2 ;;
  Linux) default_thermal=85 ;;
  *) printf '%s\n' 'load-watch.sh: use macOS or Linux' >&2; exit 2 ;;
esac

LOAD_LIMIT=${LOAD_LIMIT:-16}
THERMAL_LIMIT=${THERMAL_LIMIT:-$default_thermal}
STRAIN_MINUTES=${STRAIN_MINUTES:-10}
INTERVAL=${INTERVAL:-60}
if ! [[ $LOAD_LIMIT =~ ^[0-9]{1,6}(\.[0-9]{1,4})?$ && $THERMAL_LIMIT =~ ^[0-9]{1,4}(\.[0-9]{1,4})?$\
        && $STRAIN_MINUTES =~ ^[0-9]{1,4}$ && $INTERVAL =~ ^[0-9]{1,4}$ ]]; then
  printf '%s\n' 'load-watch.sh: invalid numeric limit or interval' >&2
  exit 2
fi
duration=$((10#$STRAIN_MINUTES * 60))
interval=$((10#$INTERVAL))
if [ "$interval" -eq 0 ]; then
  printf '%s\n' 'load-watch.sh: INTERVAL must be at least 1 second' >&2
  exit 2
fi

native_sample() {
  local load='-' thermal='-'
  case "$os" in
    Darwin)
      load=$(sysctl -n vm.loadavg 2>/dev/null | awk 'NF >= 3 { print $2 }') || load='-'
      thermal=$(notifyutil -g com.apple.system.thermalpressurelevel 2>/dev/null |
        awk '$1 == "com.apple.system.thermalpressurelevel" && $2 ~ /^[0-4]$/ { print $2 }') || thermal='-'
      ;;
    Linux)
      load=$(awk 'NR == 1 { print $1 }' /proc/loadavg 2>/dev/null) || load='-'
      local zone temp
      thermal=$(
        for zone in /sys/class/thermal/thermal_zone*/temp; do
          if [ -r "$zone" ] && read -r temp 2>/dev/null < "$zone"; then
            printf '%s\n' "$temp"
          fi
        done | awk '
          /^-?[0-9]+$/ && $1 >= -50000 && $1 <= 200000 {
            if (!valid || $1 > hottest) hottest = $1
            valid = 1
          }
          END { if (valid) printf "%.3f", hottest / 1000; else print "-" }'
      )
      ;;
  esac
  printf '%s %s\n' "${load:--}" "${thermal:--}"
}

verdict() {
  awk -v load_value="$1" -v thermal="$2" -v max_load="$LOAD_LIMIT" -v max_thermal="$THERMAL_LIMIT" '
    BEGIN {
      numeric = "^-?[0-9]+([.][0-9]+)?$"
      have_load = load_value ~ numeric && load_value + 0 >= 0
      have_thermal = thermal ~ numeric
      if ((have_load && load_value + 0 > max_load + 0) || (have_thermal && thermal + 0 >= max_thermal + 0))
        print "STRAIN"
      else if (have_load)
        print "CLEAR"
      else
        print "UNKNOWN"
    }'
}

if [ -n "$samples" ] && ! { exec 3< "$samples"; } 2>/dev/null; then
  printf '%s\n' 'load-watch.sh: could not open sample file' >&2
  exit 2
fi

first_strained=-1
fixture_time=0
while :; do
  if [ -n "$samples" ]; then
    load='' thermal='' extra=''
    if ! read -r load thermal extra <&3 && [ -z "$load" ]; then
      break
    fi
    if [ -z "$thermal" ] || [ -n "$extra" ] || \
       ! [[ $load =~ ^(-|[0-9]{1,8}(\.[0-9]{1,6})?)$ && $thermal =~ ^(-|-?[0-9]{1,8}(\.[0-9]{1,6})?)$ ]]; then
      printf '%s\n' 'load-watch.sh: invalid synthetic sample' >&2
      exit 2
    fi
    sampled_at=$fixture_time
    fixture_time=$((fixture_time + interval))
  else
    read -r load thermal <<< "$(native_sample)"
    sampled_at=$SECONDS
  fi
  status=$(verdict "$load" "$thermal")
  if [ "$once" -eq 1 ]; then
    printf '%s | load_1m=%s | thermal=%s\n' "$status" "$load" "$thermal"
    exit 0
  fi
  if [ "$status" = "STRAIN" ]; then
    if [ "$first_strained" -lt 0 ]; then
      first_strained=$sampled_at
    fi
    if [ "$((sampled_at - first_strained))" -ge "$duration" ]; then
      printf 'HOST UNDER STRAIN for %s min | load_1m=%s | thermal=%s\n' "$STRAIN_MINUTES" "$load" "$thermal"
      exit 0
    fi
  else
    first_strained=-1
  fi
  if [ -z "$samples" ]; then
    sleep "$interval"
  fi
done
