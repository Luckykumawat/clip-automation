#!/bin/bash
set -eu
host="${CLIPS_SSH_HOST:?Set CLIPS_SSH_HOST, for example root@your-vps}"
base=$(cd "$(dirname "$0")" && pwd)
local_project="${CLIPS_HISTORY_DIR:-$base/..}"
skip=$(python3 - "$local_project" "$HOME/Desktop" <<'PY'
import re
import sys
from pathlib import Path
project, desktop = map(Path, sys.argv[1:])
ids = {"91c96JtcIFg"}
for p in (project / "outputs").rglob("source-timestamps.csv"):
    ids.update(re.findall(r"[?&]v=([\w-]{11})", p.read_text()))
for p in (project / "work/n8n-clips-state/queue").glob("*.json"):
    match = re.fullmatch(r"([\w-]{11})-\d+", p.stem)
    if match:
        ids.add(match.group(1))
print(" ".join(sorted(ids)))
PY
)
stage=$(ssh "$host" 'cd /opt/clip-automation-vps && mktemp -d data/desktop-cuts.XXXXXX | cut -d/ -f2')
case "$stage" in desktop-cuts.*) ;; *) exit 1;; esac
scp "$base/cut-three-unposted.py" "$host:/opt/clip-automation-vps/data/$stage/"
status=0
ssh "$host" "cd /opt/clip-automation-vps && chown 1000:1000 'data/$stage' && docker compose exec -T clips python3 -u '/data/$stage/cut-three-unposted.py' '/data/$stage' $skip </dev/null" || status=$?
destination=$(mktemp -d "$HOME/Desktop/Three-creator-cuts.XXXXXX")
scp "$host:/opt/clip-automation-vps/data/$stage/*-5min.mp4" "$destination/"
echo "Files saved in $destination"
exit "$status"
