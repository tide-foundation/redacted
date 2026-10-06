#!/usr/bin/env bash
# Docker owns the Python runtime. Never source .env or bootstrap credentials.
set -euo pipefail
cd "$(dirname "$0")/.."
compose() { docker compose --profile secure-history "$@"; }
helper() { compose exec -T app python /app/scripts/tidecloak-container.py "$@"; }
credentials() {
    if [[ ! -f .tidecloak/bootstrap.env ]]; then
        (umask 077
         mkdir -p .tidecloak
         local temporary
         temporary=$(mktemp .tidecloak/bootstrap.XXXXXX)
         trap 'rm -f "$temporary"' EXIT
         compose run --rm -T --no-deps --entrypoint python app \
             /app/scripts/tidecloak-container.py generate > "$temporary"
         mv "$temporary" .tidecloak/bootstrap.env)
    fi
}
action=${1:-start}
[[ $# -eq 0 ]] || shift
container=''
browser=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-browser) browser=false; shift ;;
        --container) container=${2:?Supply a container name}; shift 2 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
done
# One start command chooses the installation's configured server automatically.
if [[ "$action" == start ]]; then
    kind=$(helper status) || {
        printf 'Start Redacted first: docker compose up --build --wait\n' >&2
        exit 1
    }
    case "$kind" in
        *external-configured) action=open ;;
        *external) action=connect ;;
    esac
fi
case "$action" in
    open) output=$(helper env </dev/null) ;;
    prepare) credentials; compose pull tidecloak; compose create tidecloak; exit ;;
    stop) compose stop tidecloak; exit ;;
    logs) compose logs --tail=80 tidecloak; exit ;;
    credentials) cat .tidecloak/bootstrap.env; exit ;;
    start)
        credentials
        compose up -d tidecloak
        output=$(helper env < .tidecloak/bootstrap.env)
        ;;
    connect)
        if [[ -n "$container" ]]; then
            output=$(docker inspect "$container" | helper container-env)
        else
            read -r -p 'TideCloak owner username: ' username
            read -r -s -p 'TideCloak owner password: ' password
            printf '\n'
            output=$(printf 'KC_BOOTSTRAP_ADMIN_USERNAME=%s\nKC_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$username" "$password" | helper env)
            unset password
        fi
        ;;
    *) printf 'Usage: bash scripts/tidecloak.sh {prepare|start|connect|stop|logs|credentials} [--no-browser] [--container NAME]\n' >&2; exit 1 ;;
esac
printf '%s\n' "$output"
url=''
while IFS= read -r line; do
    case "$line" in http://localhost:*|http://127.0.0.1:*) url=$line ;; esac
done <<< "$output"
if [[ "$browser" == true && -n "$url" ]]; then
    if [[ "$(uname -s)" == Darwin ]]; then
        open "$url" >/dev/null 2>&1 || true
    elif command -v wslview >/dev/null 2>&1; then
        wslview "$url" >/dev/null 2>&1 || true
    elif command -v powershell.exe >/dev/null 2>&1; then
        printf '%s' "$url" | powershell.exe -NoProfile -NonInteractive -Command \
            '$ErrorActionPreference = "Stop"; $url = [Console]::In.ReadToEnd(); Start-Process -FilePath $url' >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$url" >/dev/null 2>&1 || true
    fi
fi
printf 'Open the complete printed link in a browser on this computer. Keep private setup links secret.\n'
