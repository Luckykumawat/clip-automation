#!/bin/bash
set -eu
action=${1:-}
case "$action" in
  refresh|morning|evening) ;;
  *) echo "Usage: bash $0 refresh|morning|evening"; exit 2 ;;
esac
host="${CLIPS_SSH_HOST:?Set CLIPS_SSH_HOST, for example root@your-vps}"
cookie="$HOME/Downloads/youtube-cookies.txt"
test -s "$cookie"
stage=$(ssh "$host" 'umask 077; mktemp -d /tmp/clip-refresh.XXXXXX')
scp "$cookie" "$host:$stage/"

ssh "$host" "bash -se -- '$stage' '$action'" <<'REMOTE'
cd /opt/clip-automation-vps
stage=$1
trap 'rm -f "$stage/youtube-cookies.txt"; rmdir "$stage"' EXIT
action=$2
today=$(TZ=Asia/Kolkata date +%F)
echo "Latest progress for $today IST:"
if [ -f data/state/clip-progress.log ]; then
  grep "^$today " data/state/clip-progress.log | tail -n 20 || true
fi
(
  exec 9>>data/state/daily.lock
  if ! flock -n 9; then
    echo "A clip run is already active. No second run was started."
    exit 1
  fi
  docker compose exec -T clips sh -ec '
    umask 077
    dir=/data/youtube-auth
    tmp=$(mktemp -d "$dir/morning-test.XXXXXX")
    trap "rm -rf \"$tmp\"" EXIT
    cat > "$tmp/cookies.txt"
    grep -qm1 "Netscape HTTP Cookie File" "$tmp/cookies.txt"
    for id in ujexbDYPc2s 169ZzvPxvvM; do
      if timeout 150 yt-dlp --quiet --no-progress --js-runtimes node --no-playlist         --cookies "$tmp/cookies.txt" --download-sections "*0-47"         -f "bv[ext=mp4][height<=720][protocol=https]+ba[ext=m4a][protocol=https]/bv*[height<=1080]+ba/b[height<=1080]"         --merge-output-format mkv -o "$tmp/$id.%(ext)s"         "https://www.youtube.com/watch?v=$id" &&
        test -s "$tmp/$id.mkv"; then
        mv "$tmp/cookies.txt" "$dir/youtube-cookies.txt"
        echo "$id: download passed; scheduled cookies updated."
        exit 0
      fi
      echo "$id: download test failed."
    done
    echo "No test passed. Existing cookies preserved; retry skipped."
    exit 1
  ' < "$stage/youtube-cookies.txt"
)

if [ "$action" = refresh ]; then
  echo "Cookies refreshed. No clip run started."
  exit 0
fi
if [ "$(TZ=Asia/Kolkata date +%F)" != "$today" ]; then
  echo "IST date changed during the test. No clip run started."
  exit 1
fi
echo "Starting $today $action retry:"
docker compose exec -T clips python3 -u /opt/clips/clips.py --slot "$action" </dev/null
REMOTE
