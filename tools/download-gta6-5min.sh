#!/bin/bash
set -euo pipefail

work=$(mktemp -d "${TMPDIR:-/tmp}/gta6-clip.XXXXXX")
trap 'rm -rf "$work"' EXIT
cookies="$HOME/Downloads/youtube-cookies.txt"
destination="$HOME/Desktop/GTA6-91c96JtcIFg-5min.mp4"

test -s "$cookies" || { echo "Missing $cookies"; exit 1; }
test ! -e "$destination" || { echo "File already exists: $destination"; exit 1; }
for tool in python3 node ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "Missing $tool"; exit 1; }
done

python3 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install --disable-pip-version-check 'yt-dlp[default]==2026.11.23'
cp "$cookies" "$work/cookies.txt"
chmod 600 "$work/cookies.txt"
"$work/venv/bin/yt-dlp" --ignore-config --js-runtimes node --no-playlist \
  --cookies "$work/cookies.txt" --download-sections '*0-300' \
  -f 'bv*[height<=1080]+ba/b[height<=1080]' --merge-output-format mkv \
  -o "$work/source.%(ext)s" 'https://www.youtube.com/watch?v=91c96JtcIFg'

shopt -s nullglob
sources=("$work"/source.*)
test "${#sources[@]}" -eq 1
ffmpeg -nostdin -v error -stats -i "${sources[0]}" -t 300 \
  -map 0:v:0 -map '0:a:0?' -c:v libx264 -preset fast -crf 20 \
  -pix_fmt yuv420p -c:a aac -movflags +faststart "$work/clip.mp4"
duration=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$work/clip.mp4")
"$work/venv/bin/python" -c 'import sys; seconds = float(sys.argv[1]); assert 298 <= seconds <= 301, f"Unexpected duration: {seconds}s"' "$duration"
mv -n "$work/clip.mp4" "$destination"
test ! -e "$work/clip.mp4"
echo "Saved $duration seconds to $destination"
