#!/usr/bin/env bash
# Loads backup token from Hermes config and runs the backup script.
set -e

CONFIG="/data/.hermes/config.yaml"

# Read token from YAML config (no yaml module available; parse the line)
TOKEN="$(python3 -c "
import re
for line in open('$CONFIG'):
    m = re.search(r'^\s*backup_token:\s*(\S+)', line)
    if m:
        print(m.group(1))
        break
")"

if [ -z "$TOKEN" ]; then
  echo "ERROR: HERMES_BACKUP_TOKEN is empty."
  exit 1
fi

export HERMES_BACKUP_TOKEN="$TOKEN"
bash /data/workspace/backup_to_github.sh
