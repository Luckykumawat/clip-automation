"""Enable resilient YouTube source selection on the deployed worker."""
import os
from pathlib import Path

path = Path("clips.py")
source = path.read_text()
if "# Continue after YouTube source errors" in source:
    raise SystemExit(0)

def replace(old, new):
    global source
    if source.count(old) != 1:
        raise SystemExit(f"Unexpected deployed version near {old[:80]!r}")
    source = source.replace(old, new, 1)

replace("def candidates(config):", '''# Continue after YouTube source errors
def source_error(key, reason, notified):
    progress(STATE, f"YouTube source {key}: {reason}")
    if reason in notified:
        return
    notified.add(reason)
    try:
        secret = json.loads((STATE / "credentials.json").read_text())
        telegram_message(secret["telegram_bot_token"], secret["telegram_chat_id"],
                         f"YouTube source failed: {key} | {reason}. Trying remaining sources.")
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        progress(STATE, f"Source alert failed: {type(error).__name__}")


def candidates(config):''')
if 'for url in dict.fromkeys(config.get("youtube_video_urls", [])):' not in source:
    replace('    for channel in dict.fromkeys(channels):', '''    for url in dict.fromkeys(config.get("youtube_video_urls", [])):
        match = re.fullmatch(r"https://(?:www\\.)?youtube\\.com/watch\\?v=([A-Za-z0-9_-]{11})", url)
        if not match:
            raise ValueError("Invalid YouTube source URL")
        key = match.group(1)
        if next((STATE / "queue").glob(f"{key}-*.json"), None):
            continue
        try:
            row = json.loads(out(*ytdlp(), "--no-cache-dir", "--js-runtimes", "node",
                                 "--no-playlist", "--skip-download", "--dump-single-json", url))
        except (subprocess.CalledProcessError, ValueError):
            source_error(key, "video lookup failed", notified_sources)
            continue
        length = row.get("duration")
        if (not isinstance(length, (int, float)) or length < DURATION
                or row.get("live_status") in ("is_live", "is_upcoming")):
            source_error(key, "source is not a usable replay", notified_sources)
            continue
        seen.add(key)
        yield url, (key, None, length, row.get("title") or "YouTube video", url)
    for channel in dict.fromkeys(channels):''')
else:
    replace('            print(f"Skipping unavailable video: {url}", file=sys.stderr)',
            '            source_error(key, "video lookup failed", notified_sources)')
replace('    seen = set()', '    seen = set()\n    notified_sources = set()')
replace('    sampled = set()', '    sampled = set()\n    notified_downloads = set()')
replace('                    raise PermissionError("YouTube rejected the scheduled session") from None',
        '''                    source_error(key, "YouTube rejected the saved session", notified_downloads)
                    break
                if path is None and isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
                    source_error(key, "segment download failed", notified_downloads)''')
if '((url in direct_sources, score),' not in source:
    replace('ranked.append((score, group, key, start,',
            'ranked.append(((url in set(config.get("youtube_video_urls", [])), score), group, key, start,')
compile(source, str(path), "exec")
staged = path.with_suffix(".url-dual.tmp")
staged.write_text(source)
staged.chmod(path.stat().st_mode & 0o777)
os.replace(staged, path)
print("URL fallback patch installed")
