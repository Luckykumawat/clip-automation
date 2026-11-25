#!/bin/bash
set -eu
host="${CLIPS_SSH_HOST:?Set CLIPS_SSH_HOST, for example root@your-vps}"
base=$(cd "$(dirname "$0")" && pwd)
cookie="$HOME/Downloads/youtube-cookies.txt"
test -s "$cookie"
stage=$(ssh "$host" 'mktemp -d /tmp/clip-dual.XXXXXX')
scp "$base/clip-url-dual.py" "$cookie" "$host:$stage/"

ssh "$host" "bash -se -- '$stage'" <<'REMOTE'
cd /opt/clip-automation-vps
stage=$1
umask 077
trap 'rm -f "$stage/youtube-cookies.txt"' EXIT
grep -qm1 'Netscape HTTP Cookie File' "$stage/youtube-cookies.txt"
exec 9>>data/state/daily.lock
echo "Waiting for active clip runs..."
flock -w 900 9

docker compose exec -T clips sh -ec '
  umask 077
  dir=/data/youtube-auth
  tmp=$(mktemp -d "$dir/dual-test.XXXXXX")
  trap "rm -rf \"$tmp\"" EXIT
  cat > "$tmp/youtube-cookies.txt"
  passed=0
  for id in ujexbDYPc2s 169ZzvPxvvM; do
    if timeout 180 yt-dlp --no-cache-dir --js-runtimes node --no-playlist       --cookies "$tmp/youtube-cookies.txt" --download-sections "*0-47"       --merge-output-format mkv -o "$tmp/$id.%(ext)s"       "https://www.youtube.com/watch?v=$id" &&
      test -n "$(find "$tmp" -type f -name "$id.*" ! -name "*.part" -size +0c -print -quit)"; then
      echo "$id: source download passed"
      passed=1
    else
      echo "$id: source download failed"
    fi
  done
  if [ "$passed" -ne 1 ]; then
    echo "Both tests failed. Live mode was not changed."
    exit 1
  fi
  mv "$tmp/youtube-cookies.txt" "$dir/youtube-cookies.txt"
' < "$stage/youtube-cookies.txt"

backup=$(mktemp -d /opt/clip-url-dual-backup.XXXXXX)
cp -p clips.py data/config.json "$backup/"
python3 "$stage/clip-url-dual.py"
docker compose cp clips.py clips:/tmp/clips-url-dual.py </dev/null
docker compose exec -T --user 0:0 clips sh -ec '
  install -m 644 /tmp/clips-url-dual.py /opt/clips/.clips-new
  mv /opt/clips/.clips-new /opt/clips/clips.py
' </dev/null
docker compose exec -T clips python3 -c 'import clips; assert callable(clips.source_error)' </dev/null

python3 - <<'PY'
import json
import os
import tempfile
from pathlib import Path

path = Path("data/config.json")
stat = path.stat()
config = json.loads(path.read_text())
urls = [f"https://www.youtube.com/watch?v={i}"
        for i in ("ujexbDYPc2s", "169ZzvPxvvM")]
config["youtube_video_urls"] = list(dict.fromkeys(config.get("youtube_video_urls", []) + urls))
config["instagram_only"] = False
with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
    json.dump(config, file, indent=2)
    file.write("\n")
    os.fchown(file.fileno(), stat.st_uid, stat.st_gid)
    os.fchmod(file.fileno(), stat.st_mode & 0o777)
os.replace(file.name, path)
PY

docker compose exec -T clips python3 -c '
import clips, json
config = json.loads(clips.CONFIG.read_text())
assert config["instagram_only"] is False and callable(clips.source_error)
print("YouTube + Instagram mode enabled")
' </dev/null
REMOTE
