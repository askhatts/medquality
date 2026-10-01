#!/bin/sh
set -eu

state_dir=/run/quality-watchdog
mkdir -p "$state_dir"

probe() {
    curl --silent --show-error --fail --location --max-time 7 \
        --resolve oncomap-abai.kz:443:127.0.0.1 \
        "https://oncomap-abai.kz$1" --output /dev/null
}

probe_onco() {
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' deploy-backend-1 2>/dev/null || true)
    if [ "$health" = none ]; then
        probe /
    else
        [ "$health" = healthy ]
    fi
}

check() {
    name=$1
    url=$2
    container=$3
    state="$state_dir/$name"
    if { [ "$name" = onco ] && probe_onco; } || { [ "$name" != onco ] && probe "$url"; }; then
        printf '0 0\n' > "$state"
        return 0
    fi
    count=0
    last_restart=0
    if [ -f "$state" ]; then
        read -r count last_restart < "$state" || true
    fi
    count=$((count + 1))
    now=$(date +%s)
    if [ "$count" -ge 3 ] && [ "$((now - last_restart))" -ge 600 ]; then
        logger -t quality-watchdog "Restarting $container after $count failed local probes"
        docker restart "$container" >/dev/null
        printf '0 %s\n' "$now" > "$state"
    else
        printf '%s %s\n' "$count" "$last_restart" > "$state"
    fi
}

# If every route fails at once, the proxy is the likeliest single point of failure.
if ! probe / && ! probe /quality/health/ && ! probe /applications/; then
    check caddy / deploy-caddy-1
    exit 0
fi

check onco / deploy-backend-1
check quality /quality/health/ quality_app
check requests /applications/ requests_app
