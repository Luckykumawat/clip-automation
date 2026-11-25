#!/bin/bash
set -eu

if [ "${1:-}" != "--vps" ]; then
  host="${CLIPS_SSH_HOST:?Set CLIPS_SSH_HOST, for example root@your-vps}"
  base=$(cd "$(dirname "$0")" && pwd)
  stage=$(ssh "$host" 'umask 077; mktemp -d /tmp/clip-drive.XXXXXX')
  scp "$base/telegram-drive-worker.py" "$base/install-telegram-drive.sh" "$host:$stage/"
  ssh -t "$host" "bash '$stage/install-telegram-drive.sh' --vps"
  exit
fi

cd /opt/clip-automation-vps
docker compose exec -T clips python3 -c 'import clips, publish; assert callable(publish.telegram_message) and callable(publish.progress)' </dev/null
python3 - <<'PY'
import getpass
import json
import os
import tempfile
from pathlib import Path

file = Path("data/state/credentials.json")
original = file.stat()
credentials = json.loads(file.read_text())
for field in ("telegram_bot_token", "telegram_chat_id", "gemini_api_key"):
    if not credentials.get(field):
        raise SystemExit(f"Missing {field} in VPS credentials")
if not credentials.get("drive_download_api_key"):
    key = getpass.getpass("Google Drive API key (hidden): ").strip()
    if not key:
        raise SystemExit("No Drive key entered")
    credentials["drive_download_api_key"] = key
    with tempfile.NamedTemporaryFile(mode="w", dir=file.parent, delete=False) as temp:
        json.dump(credentials, temp, indent=2)
        temp.write("\n")
        os.fchown(temp.fileno(), original.st_uid, original.st_gid)
        os.fchmod(temp.fileno(), 0o600)
    os.replace(temp.name, file)
PY
base=$(cd "$(dirname "$0")" && pwd)
install -m 644 "$base/telegram-drive-worker.py" data/.telegram-drive-worker.new
mv data/.telegram-drive-worker.new data/telegram-drive-worker.py
docker_path=$(command -v docker)
cat > /etc/systemd/system/clip-telegram-drive.service <<UNIT
[Unit]
Description=Telegram Drive inbox for the clip worker
After=docker.service
Requires=docker.service

[Service]
WorkingDirectory=/opt/clip-automation-vps
ExecStart=$docker_path compose exec -T clips python3 -u /data/telegram-drive-worker.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now clip-telegram-drive.service
systemctl is-active clip-telegram-drive.service
echo "Send /status to your bot to verify it can receive and answer messages."
