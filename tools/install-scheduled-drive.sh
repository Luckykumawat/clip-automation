#!/bin/bash
set -eu
host="${CLIPS_SSH_HOST:?Set CLIPS_SSH_HOST, for example root@your-vps}"
base=$(cd "$(dirname "$0")" && pwd)
stage=$(ssh "$host" 'umask 077; mktemp -d /tmp/clip-drive-schedule.XXXXXX')
scp "$base/telegram-drive-worker.py" "$base/patch-drive-schedule.py" "$host:$stage/"
ssh "$host" "bash -se -- '$stage'" <<'REMOTE'
cd /opt/clip-automation-vps
stage=$1
systemctl is-active --quiet clip-telegram-drive.service
exec 9>>data/state/daily.lock
flock -w 900 9
backup=$(mktemp -d /opt/clip-drive-schedule-backup.XXXXXX)
cp -p clips.py data/telegram-drive-worker.py "$backup/"
python3 "$stage/patch-drive-schedule.py"
install -m 644 "$stage/telegram-drive-worker.py" data/.telegram-drive-worker.new
mv data/.telegram-drive-worker.new data/telegram-drive-worker.py
docker compose cp clips.py clips:/tmp/clips-drive-schedule.py </dev/null
docker compose exec -T --user 0:0 clips sh -ec '
  install -m 644 /tmp/clips-drive-schedule.py /opt/clips/.clips-new
  mv /opt/clips/.clips-new /opt/clips/clips.py
' </dev/null
docker compose exec -T clips python3 -c '
import clips, runpy
assert "run_scheduled_slot" in open(clips.__file__).read()
assert callable(runpy.run_path("/data/telegram-drive-worker.py")["drive_slot_open"])
' </dev/null
systemctl restart clip-telegram-drive.service
systemctl is-active clip-telegram-drive.service
echo "Scheduled Drive queue enabled"
REMOTE
