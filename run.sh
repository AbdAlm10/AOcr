#!/usr/bin/env bash
# Start AOcr on this computer and print the address for your phone.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

WEB_PORT="${WEB_PORT:-8787}"
API_PORT="${PORT:-8791}"
export WEB_PORT PORT="$API_PORT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required (3.10 or newer)." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js 20 or newer is required for the desk. Install it, then run this again." >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Note: ffmpeg is not installed. Normal PDFs and photos still work. Images above about 32 megapixels need ffmpeg."
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q -U pip
if [[ "${CHANDRA_ENGINE:-auto}" == "hf" ]]; then
  python -m pip install -q -e "./server[hf]"
else
  python -m pip install -q -e ./server
fi

if [[ ! -d web/node_modules ]]; then
  npm install --prefix web
fi

if [[ -z "${ADVERTISED_HOSTS:-}" ]]; then
  hosts=""
  if command -v hostname >/dev/null 2>&1; then
    for ip in $(hostname -I 2>/dev/null || true); do
      case "$ip" in
        127.*) ;;
        *) hosts="${hosts:+$hosts,}$ip" ;;
      esac
    done
  fi
  export ADVERTISED_HOSTS="$hosts"
fi
export API_INTERNAL_URL="http://127.0.0.1:${API_PORT}"

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then kill "$API_PID" 2>/dev/null || true; fi
  if [[ -n "${WEB_PID:-}" ]]; then kill "$WEB_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

python -m waraq &
API_PID=$!

for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:${API_PORT}/api/health" >/dev/null; then
    break
  fi
  sleep 0.2
done

(
  cd "$ROOT/web"
  if [[ "${CHANDRA_DEV:-}" == "1" ]]; then
    exec npx next dev --turbopack --hostname 0.0.0.0 --port "$WEB_PORT"
  fi
  npm run build
  exec npx next start --hostname 0.0.0.0 --port "$WEB_PORT"
) &
WEB_PID=$!

echo ""
echo "  AOcr is starting"
echo "  This computer    http://127.0.0.1:${WEB_PORT}"
IFS=',' read -r -a ips <<< "${ADVERTISED_HOSTS}"
found=0
for ip in "${ips[@]}"; do
  [[ -z "$ip" ]] && continue
  found=1
  echo "  Your phone       http://${ip}:${WEB_PORT}"
done
if [[ "$found" == "0" ]]; then
  echo "  Your phone       http://<this-computer-lan-ip>:${WEB_PORT}"
fi
echo ""
echo "  Phone: same Wi-Fi, open the phone address above. Do not use localhost on the phone."
echo "  If it does not load, allow TCP port ${WEB_PORT} in the firewall."
echo "  Leave this window open."
echo ""

wait "$WEB_PID"
