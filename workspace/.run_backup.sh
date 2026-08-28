#!/usr/bin/env bash
set -euo pipefail
TOKEN="$(grep 'backup_token:' /data/.hermes/config.yaml | sed -E 's/.*backup_token:\s*//' | sed 's/__REDACTED__//')"
export HERMES_BACKUP_TOKEN="$TOKEN"
echo "token length: ${#TOKEN}"
exec bash /data/workspace/backup_to_github.sh
