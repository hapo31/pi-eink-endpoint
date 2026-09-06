#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$PROJECT_DIR/.env"

if [[ -z "${RASPI_HOST:-}" ]]; then
  echo "RASPI_HOST is not set in .env file" >&2
  exit 1
fi

if [[ -z "${RASPI_USER:-}" ]]; then
  echo "RASPI_USER is not set in .env file" >&2
  exit 1
fi

if [[ -z "${RASPI_KEY_PATH:-}" ]]; then
  echo "RASPI_KEY_PATH is not set in .env file" >&2
  exit 1
fi

ssh -i "$RASPI_KEY_PATH" "$RASPI_USER@$RASPI_HOST" 'bash -s' <<'REMOTE_COMMAND'
set -euo pipefail

cd "$HOME/eink-endpoint"
git pull --ff-only
git submodule update --init --recursive
SERVICE_USER="$(id -un)"
sudo systemctl restart "pi-eink-endpoint@$SERVICE_USER.service"
REMOTE_COMMAND
