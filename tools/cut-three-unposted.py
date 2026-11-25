"""Export one five-minute cut per creator using unused source videos."""
import json
import re
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1])
skip = set(sys.argv[2:])
for path in Path("/data/state/queue").glob("*.json"):
    item = json.loads(path.read_text())
    match = re.match(r"^([\w-]{11})-\d+$", item.get("id", path.stem))
    if match:
        skip.add(match.group(1))
    source = item.get("source_url") or ""
    match = re.search(r"[?&]v=([\w-]{11})", source)
    if match:
        skip.add(match.group(1))

command = ["yt-dlp", "--no-cache-dir", "--js-runtimes", "node"]
cookies = Path("/data/youtube-auth/youtube-cookies.txt")
if cookies.is_file():
    command += ["--cookies", str(cookies)]
creators = (("CarryisLive", "CarryMinati"), ("IShowSpeed", "IShowSpeed"), ("Mythpat", "Mythpat"))
failures = []
for handle, label in creators:
    try:
        raw = subprocess.check_output(
            [*command, "--flat-playlist", "--playlist-end", "15", "--dump-single-json",
             f"https://www.youtube.com/@{handle}/streams"], text=True, timeout=90)
        entries = json.loads(raw).get("entries") or []
    except (subprocess.SubprocessError, ValueError):
        print(f"{label}: could not read source list", flush=True)
        failures.append(label)
        continue
    for entry in entries:
        if not entry or entry.get("live_status") in {"is_live", "is_upcoming"}:
            continue
        video_id, duration = entry.get("id"), entry.get("duration")
        if (not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
                or video_id in skip or not isinstance(duration, (int, float)) or duration < 305):
            continue
        start = min(int(duration * 0.20), int(duration) - 303)
        url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"{label}: testing unused source {video_id}, {start}–{start + 300}s", flush=True)
        source = out / f"{label}-source.mkv"
        target = out / f"{label}-{video_id}-5min.mp4"
        try:
            subprocess.run([*command, "--quiet", "--no-playlist", "--download-sections",
                            f"*{start}-{start + 302}", "-f", "bv*[height<=720]+ba/b[height<=720]",
                            "--merge-output-format", "mkv", "--remux-video", "mkv",
                            "-o", str(source), url], check=True, timeout=600)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
                            "-t", "300", "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "21", "-c:a", "aac",
                            "-movflags", "+faststart", str(target)], check=True, timeout=900)
            actual = float(subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nk=1:nw=1", str(target)], text=True))
            if not 298 <= actual <= 301:
                raise ValueError(f"Duration check failed: {actual}s")
        except (subprocess.SubprocessError, ValueError):
            target.unlink(missing_ok=True)
            continue
        finally:
            source.unlink(missing_ok=True)
        skip.add(video_id)
        print(f"{label}: verified {actual:.1f}s | {url}&t={start}s", flush=True)
        break
    else:
        print(f"{label}: no unused downloadable source found", flush=True)
        failures.append(label)
if failures:
    print("Missing: " + ", ".join(failures), flush=True)
    raise SystemExit(1)
