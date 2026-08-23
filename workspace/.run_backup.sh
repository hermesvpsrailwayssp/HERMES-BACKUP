#!/usr/bin/env bash
set -euo pipefail
cd /data/workspace
TOKEN="$(grep 'backup_token:' /data/.hermes/config.yaml | sed -E 's/.*backup_token:\s*//; s/__REDACTED__//')"
export HERMES_BACKUP_TOKEN="$TOKEN"
echo "Token length: ${#TOKEN}"
bash backup_to_github.sh
