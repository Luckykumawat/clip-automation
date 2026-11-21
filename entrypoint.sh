#!/bin/sh
set -eu
if [ "${1:-}" = init ]; then
  python3 - <<'PY'
import json
from pathlib import Path
workflow = json.loads(Path('/opt/clips/daily-workflow.json').read_text())
for node in workflow['nodes']:
    if node['type'] == 'n8n-nodes-base.executeCommand':
        node['parameters']['command'] = 'python3 /opt/clips/clips.py --slot evening'
Path('/tmp/cloud-workflow.json').write_text(json.dumps(workflow))
PY
  n8n import:workflow --input=/tmp/cloud-workflow.json
  exec n8n publish:workflow --id=DailyVerticalClip01
fi
exec n8n start
