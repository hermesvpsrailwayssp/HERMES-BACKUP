#!/usr/bin/env bash
#
# backup_to_github.sh
# Pushes Hermes memory, redacted config, key workspace files, and cron job
# definitions to the GitHub repo HERMES-BACKUP as a timestamped snapshot dir.
#
# Expects HERMES_BACKUP_TOKEN in the environment (a GitHub PAT).
# The stored token may carry a trailing "__REDACTED__" artifact (from hermes
# config redaction); we strip it so authentication actually works.
#
set -euo pipefail

# --- Auth / repo config -----------------------------------------------------
TOKEN="${HERMES_BACKUP_TOKEN:-}"
TOKEN="${TOKEN%__REDACTED__}"            # strip artifact if present
if [[ -z "$TOKEN" ]]; then
  echo "ERROR: HERMES_BACKUP_TOKEN is empty." >&2
  exit 1
fi

OWNER="hermesvpsrailwayssp"
REPO="HERMES-BACKUP"
BRANCH="main"
API="https://api.github.com/repos/${OWNER}/${REPO}"

# --- Locate source paths ----------------------------------------------------
HERMES_HOME="${HERMES_HOME:-/data/.hermes}"
WORKSPACE="${WORKSPACE:-/data/workspace}"
SNAPSHOT="snapshot_$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- Build a temp staging area ---------------------------------------------
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# 1) Redacted config.yaml
mkdir -p "$STAGE/$SNAPSHOT/config"
python3 - "$HERMES_HOME/config.yaml" "$STAGE/$SNAPSHOT/config/config.yaml" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    txt = f.read()
# Redact sensitive values entirely (do not leak prefixes).
txt = re.sub(r'(monitoring_bot_token:\s*).*', r'\1***REDACTED***', txt)
txt = re.sub(r'(backup_token:\s*).*', r'\1***REDACTED***', txt)
with open(dst, "w") as f:
    f.write(txt)
PY

# 2) Cron job definitions
mkdir -p "$STAGE/$SNAPSHOT/cron"
cp "$HERMES_HOME/cron/jobs.json" "$STAGE/$SNAPSHOT/cron/jobs.json"

# 3) Memories (redact any inline secrets, e.g. the clore.ai API key)
mkdir -p "$STAGE/$SNAPSHOT/memories"
if [[ -f "$HERMES_HOME/memories/USER.md" ]]; then
  python3 - "$HERMES_HOME/memories/USER.md" "$STAGE/$SNAPSHOT/memories/USER.md" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    txt = f.read()
# Redact the clore.ai API key pattern if present.
txt = re.sub(r'\b[A-Za-z0-9_]{20,}\b(?=.*clore|clore)', '***REDACTED***', txt)
# Generic: any long alphanumeric secret-looking token of 30+ chars.
txt = re.sub(r'\b[A-Za-z0-9_]{30,}\b', '***REDACTED***', txt)
with open(dst, "w") as f:
    f.write(txt)
PY
fi
if [[ -f "$HERMES_HOME/memories/USER.md.lock" ]]; then
  cp "$HERMES_HOME/memories/USER.md.lock" "$STAGE/$SNAPSHOT/memories/USER.md.lock"
fi

# 4) Key workspace files (everything tracked in the workspace dir)
mkdir -p "$STAGE/$SNAPSHOT/workspace"
shopt -s nullglob dotglob
for f in "$WORKSPACE"/*; do
  if [[ -f "$f" ]]; then
    cp "$f" "$STAGE/$SNAPSHOT/workspace/$(basename "$f")"
  fi
done
shopt -u nullglob dotglob

# --- Push to GitHub via Git Data API (single commit) ------------------------
python3 - "$TOKEN" "$OWNER" "$REPO" "$BRANCH" "$STAGE" "$SNAPSHOT" <<'PY'
import base64, json, os, sys, urllib.request

token, owner, repo, branch, stage, snapshot = sys.argv[1:7]
api = f"https://api.github.com/repos/{owner}/{repo}"
hdr = {"Authorization": f"Bearer {token}",
       "Accept": "application/vnd.github+json",
       "User-Agent": "hermes-backup"}

def req(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=hdr, method=method)
    with urllib.request.urlopen(r) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}

# Current branch head
head = req("GET", f"{api}/git/ref/heads/{branch}")
base_commit = head["object"]["sha"]
base_tree = req("GET", f"{api}/git/commits/{base_commit}")["tree"]["sha"]

# Walk the staging area's parent ($STAGE) and create blobs whose repo paths
# retain the snapshot subdir prefix (e.g. snapshot_2026-08-31T12:16:46Z/config/config.yaml).
# We MUST walk $STAGE (the parent), not $STAGE/$SNAPSHOT, so os.path.relpath
# yields paths beginning with the snapshot dirname; otherwise entries like
# config/config.yaml collide with pre-existing root-level paths, the new
# tree is identical to base_tree, and the snapshot is silently dropped.
entries = []
for root, _, files in os.walk(stage):
    for name in files:
        full = os.path.join(root, name)
        with open(full, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        blob = req("POST", f"{api}/git/blobs",
                    {"content": content, "encoding": "base64"})
        rel = os.path.relpath(full, stage)
        entries.append({"path": rel, "mode": "100644",
                        "type": "blob", "sha": blob["sha"]})

# New tree built on top of the existing root tree
tree = req("POST", f"{api}/git/trees",
           {"base_tree": base_tree, "tree": entries})

# New commit
commit = req("POST", f"{api}/git/commits",
             {"message": f"backup: {snapshot}",
              "tree": tree["sha"], "parents": [base_commit]})

# Point branch at the new commit
req("PATCH", f"{api}/git/refs/heads/{branch}", {"sha": commit["sha"]})
print(f"Pushed backup: {snapshot}")
PY
