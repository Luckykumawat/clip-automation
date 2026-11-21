#!/usr/bin/env python3
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("CLIPS_STATE_DIR", ROOT.parents[1] / "work" / "n8n-clips-state")).resolve()
EXPORTS = Path(os.environ.get("CLIPS_EXPORT_DIR", ROOT / "clips")).resolve()
RECORDINGS = Path(os.environ.get("CLIPS_RECORDINGS_DIR", ROOT / "recordings")).resolve()
CONFIG = Path(os.environ.get("CLIPS_CONFIG", ROOT / "config.json"))
DURATION = 45
MIN_DURATION = 40
INDIA = ZoneInfo("Asia/Kolkata")


def out(*args):
    return subprocess.check_output(args, text=True).strip()


def probe(path):
    return json.loads(out("ffprobe", "-v", "error", "-show_streams",
                          "-show_format", "-of", "json", str(path)))


def duration(path):
    return float(probe(path)["format"]["duration"])


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def candidates(config):
    inbox = RECORDINGS
    recordings = () if config.get("one_clip_per_creator") else inbox.iterdir()
    for path in sorted(recordings, key=lambda p: p.stat().st_mtime, reverse=True):
        if path.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm"}:
            continue
        with path.open("rb") as source:
            key = "local-" + hashlib.sha256(source.read(1024 * 1024)).hexdigest()[:24]
        yield "local", (key, path, duration(path), path.stem, None)
    channels = config.get("channel_streams_urls")
    if channels is None:
        channels = [config["channel_streams_url"]] if config.get("channel_streams_url") else []
    seen = set()
    for channel in dict.fromkeys(channels):
        pattern = r"https://(?:www\.)?youtube\.com/@[\w.-]+/streams/?"
        if not re.fullmatch(pattern, channel):
            raise ValueError("Expected a YouTube /@handle/streams URL")
        try:
            listing = out("yt-dlp", "--no-cache-dir", "--flat-playlist", "--playlist-end", "5",
                          "--dump-single-json", channel)
            entries = json.loads(listing).get("entries", [])
        except (subprocess.CalledProcessError, ValueError):
            print(f"Skipping unavailable channel: {channel}", file=sys.stderr)
            continue
        for row in entries:
            if not row:
                continue
            key = row.get("id") or ""
            length = row.get("duration")
            if (not re.fullmatch(r"[A-Za-z0-9_-]{11}", key) or key in seen
                    or row.get("live_status") in ("is_live", "is_upcoming")
                    or not isinstance(length, (int, float)) or length < DURATION):
                continue
            seen.add(key)
            yield channel, (key, None, length, row.get("title") or "Livestream",
                            "https://www.youtube.com/watch?v=" + key)


def activity(path, start):
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-ss", str(start), "-i", str(path),
         "-t", str(DURATION), "-vn", "-af", "silencedetect=noise=-34dB:d=0.6,volumedetect",
         "-f", "null", "-"], capture_output=True, text=True, check=True)
    log = run.stderr
    silence = sum(map(float, re.findall(r"silence_duration: ([\d.]+)", log)))
    match = re.search(r"mean_volume: (-?[\d.]+)", log)
    volume = float(match[1]) if match else -90
    return volume - silence / 2


def render(source, start, target, facecam):
    if facecam:
        video = (
            "[0:v]scale=1920:1080,setsar=1,split=2[g][f];"
            "[g]crop=1300:1080:394:0,scale=1080:898[g];"
            "[f]crop=380:238:4:462,scale=568:356,"
            "pad=1080:1920:256:218:color=0x0F1217[f];"
            "[f][g]overlay=0:626,fps=30[v]"
        )
    else:
        video = (
            "[0:v]split[b][m];[b]scale=540:960:force_original_aspect_ratio=increase,"
            "crop=540:960,boxblur=18:4,scale=1080:1920[b];"
            "[m]scale=1080:1600:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2[m];[b][m]overlay=(W-w)/2:(H-h)/2,fps=30,setsar=1[v]"
        )
    temp = target.with_suffix(".tmp.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", str(start), "-i", str(source),
         "-t", str(DURATION), "-filter_complex", video, "-map", "[v]", "-map", "0:a:0?",
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-af", "loudnorm=I=-15:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k",
         "-ar", "48000", "-movflags", "+faststart", str(temp)], check=True)
    info = probe(temp)
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    if (stream["width"], stream["height"]) != (1080, 1920) or not MIN_DURATION <= float(stream["duration"]) <= 50:
        raise RuntimeError("Export failed resolution or minimum-duration validation")
    temp.replace(target)


def generate(config, date, test_path, target_count=3, slot="test", used_creators=()):
    with tempfile.TemporaryDirectory(prefix="samples-", dir=STATE) as scratch:
        return select_clips(config, date, test_path, target_count, slot, Path(scratch), used_creators)


def available_starts(key, length):
    if length < DURATION:
        return []
    records = (STATE / "queue").glob(f"{key}-*.json")
    used = [json.loads(record.read_text()) for record in records]
    windows = [(item["start"], item.get("end") or item["start"] + 65) for item in used]
    return [start for start in sorted({round((length - DURATION) * i / 23) for i in range(24)})
            if all(start >= end or start + DURATION <= begin for begin, end in windows)]


def select_clips(config, date, test_path, target_count, slot, scratch, used_creators):
    sources = ([("test", ("test", test_path, duration(test_path), "Local test", None))]
               if test_path else candidates(config))
    count = len(config.get("channel_streams_urls") or [None])
    samples_per_channel = max(2, 6 // count)
    ranked = []
    sampled = set()
    for group, (key, path, length, title, url) in sources:
        if group in sampled or group in used_creators:
            continue
        starts = available_starts(key, length)
        if not starts:
            continue
        if path is None and len(starts) > samples_per_channel:
            starts = [starts[round((len(starts) - 1) * i / (samples_per_channel + 1))]
                      for i in range(1, samples_per_channel + 1)]
        before = len(ranked)
        for start in starts:
            sample, clip_start = path, start
            try:
                if sample is None:
                    sample = scratch / f"{key}-{start}.mkv"
                    subprocess.run(
                        ["yt-dlp", "--no-cache-dir", "--js-runtimes", "node", "--no-playlist",
                         "--quiet", "--download-sections", f"*{start}-{start + DURATION + 2}",
                         "-f", "bv[ext=mp4][height<=720][protocol=https]+ba[ext=m4a][protocol=https]/bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format",
                         "mkv", "-o", str(sample), url],
                        check=True, timeout=300, stderr=subprocess.DEVNULL)
                    clip_start = 0
                if duration(sample) - clip_start >= MIN_DURATION:
                    ranked.append((activity(sample, clip_start), group, key, start,
                                   sample, clip_start, title, url))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
                print(f"Skipping unavailable source window {key} at {start}s", file=sys.stderr)
        if len(ranked) > before:
            sampled.add(group)
    selected = []
    chosen_creators = set()
    one_each = config.get("one_clip_per_creator") and not test_path
    for _, group, key, start, sample, clip_start, title, url in sorted(ranked, reverse=True):
        if one_each and group in chosen_creators:
            continue
        if any(key == old_key and abs(start - old_start) < DURATION
               for _, old_key, old_start, *_ in selected):
            continue
        selected.append((group, key, start, sample, clip_start, title, url))
        chosen_creators.add(group)
        if len(selected) == target_count:
            break
    result = []
    queue = STATE / ("test" if test_path else "queue")
    queue.mkdir(exist_ok=True)
    exports = queue if test_path else EXPORTS
    exports.mkdir(exist_ok=True)
    for group, key, start, sample, clip_start, title, url in sorted(selected):
        identifier = f"{key}-{start}"
        video = exports / f"{identifier}.mp4"
        render(sample, clip_start, video, config.get("ishowspeed_facecam", False) and not test_path)
        timestamp = str(dt.timedelta(seconds=start))
        item = dict(id=identifier, date=date, slot=slot, creator=group,
                    start=start, end=start + DURATION,
                    path=str(video),
                    title=f"{title[:75]} | {timestamp}",
                    caption=f"Livestream highlight from {timestamp}. " + (f"Source: {url}" if url else "")
                    + "\n\n#Shorts #GamingClips", youtube_id=None, instagram_id=None)
        write_json(queue / f"{identifier}.json", item)
        result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--test", type=Path)
    modes.add_argument("--instagram-draft", type=Path)
    parser.add_argument("--slot", choices=("morning", "evening"))
    args = parser.parse_args()
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    (STATE / "queue").mkdir(exist_ok=True)
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG.read_text())
    now = dt.datetime.now(INDIA)
    date = now.date().isoformat()
    slot = args.slot or ("morning" if now.hour < 17 else "evening")
    with (STATE / "daily.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.instagram_draft:
            from buffer_reels import create_reel
            secret_file = STATE / "credentials.json"
            if secret_file.stat().st_mode & 0o077:
                raise RuntimeError("Set credentials.json to file mode 600")
            secret = json.loads(secret_file.read_text())
            required = ("buffer_api_key", "cloudinary_cloud_name",
                        "cloudinary_api_key", "cloudinary_api_secret")
            if any(not secret.get(key) for key in required):
                raise RuntimeError("Complete Buffer and Cloudinary credentials first")
            info = probe(args.instagram_draft)
            stream = next(s for s in info["streams"] if s["codec_type"] == "video")
            if (stream["width"], stream["height"]) != (1080, 1920) or not MIN_DURATION <= float(stream["duration"]) <= 50:
                raise ValueError("Reel test requires a 1080 × 1920 clip of 40–50 seconds")
            data = args.instagram_draft.read_bytes()
            identifier = "instagram-draft-" + hashlib.sha256(data).hexdigest()[:16]
            state = STATE / f"{identifier}.json"
            item = (json.loads(state.read_text()) if state.exists() else
                    {"id": identifier, "caption": "Daily Vertical Clips – Instagram upload test"})
            post_id = create_reel(item, data, config, secret,
                                  lambda: write_json(state, item), draft=True)
            print(json.dumps({"buffer_post_id": post_id, "status": item["buffer_post_status"]}))
            return
        if args.test:
            print(json.dumps({"test": generate(config, date, args.test, 2)}))
            return
        items = [json.loads(p.read_text()) for p in (STATE / "queue").glob("*.json")]
        current = [item for item in items if item["date"] == date and item.get("slot") == slot]
        remaining = 3 - len(current)
        if remaining > 0:
            used_creators = ({item.get("creator") for item in current}
                             if config.get("one_clip_per_creator") else ())
            current.extend(generate(config, date, None, remaining, slot, used_creators))
        verification = []
        publish_from = config.get("auto_publish_from")
        auto_publish = config.get("auto_publish", False) and (
            not publish_from or now.date() >= dt.date.fromisoformat(publish_from)
        )
        if auto_publish:
            for item in current:
                item["path"] = str(EXPORTS / f'{item["id"]}.mp4')
            from publish import publish_pending, verify_publications
            try:
                publish_pending(current, STATE / "queue", config, write_json, date)
            finally:
                verification = verify_publications(current, STATE / "queue", config, write_json)
                print(json.dumps({"date": date, "slot": slot, "verified": verification}))
        print(json.dumps({"date": date, "slot": slot, "clips": [item["id"] for item in current],
                          "auto_publish": auto_publish}))


if __name__ == "__main__":
    main()
