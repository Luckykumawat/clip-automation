"""Add Instagram-only mode to the deployed clip worker."""
import os
from pathlib import Path

def edit(source, old, new):
    if source.count(old) != 1:
        raise SystemExit(f"Unexpected worker version near {old[:70]!r}")
    return source.replace(old, new, 1)

clips = Path("clips.py").read_text()
publish = Path("publish.py").read_text()
marker = "# Instagram-only publishing"
if marker in clips and marker in publish:
    print("Instagram-only source patch already installed")
    raise SystemExit(0)

clips = edit(clips, '    recordings = () if config.get("one_clip_per_creator") else inbox.iterdir()', '''    # Instagram-only publishing
    local_only = config.get("instagram_only", False)
    recordings = inbox.iterdir() if local_only or not config.get("one_clip_per_creator") else ()''')
clips = edit(clips, '''        yield "local", (key, path, duration(path), path.stem, None)
    channels =''','''        yield key if local_only else "local", (key, path, duration(path), path.stem, None)
    if local_only:
        return
    channels =''')
clips = edit(clips, 'if path is None and len(starts) > samples_per_channel:', 'if (path is None or config.get("instagram_only")) and len(starts) > samples_per_channel:')
clips = edit(clips, '        if not current:\n            raise RuntimeError("No usable clips generated")', '''        if not current:
            if config.get("instagram_only"):
                raise RuntimeError("No usable footage in the VPS recordings inbox")
            raise RuntimeError("No usable clips generated")''')
clips = edit(clips, '            if config.get("youtube_privacy") == "public" and config.get("instagram_provider") == "buffer":', '''            if config.get("instagram_provider") == "buffer":''')
clips = edit(clips, '                if any(not item.get("youtube_public_verified") or item.get("buffer_post_status") != "sent"', '''                require_youtube = not config.get("instagram_only") and config.get("youtube_privacy") == "public"
                if any((require_youtube and not item.get("youtube_public_verified"))
                       or item.get("buffer_post_status") != "sent"''')
clips = edit(clips, 'raise RuntimeError("Publishing could not be verified on both platforms")', '''raise RuntimeError("Publishing could not be verified on configured platforms")''')
clips = edit(clips, '                "Publishing could not be verified on both platforms",', '''                "Publishing could not be verified on configured platforms",
                "No usable footage in the VPS recordings inbox",''')
publish = edit(publish, '    keys = {"google_client_id", "google_client_secret", "google_refresh_token"}', '''    # Instagram-only publishing
    instagram_only = config.get("instagram_only", False)
    google_keys = {"google_client_id", "google_client_secret", "google_refresh_token"}
    keys = set()''')
publish = edit(publish, '    problems = []\n    for item in sorted(items,', '''    problems = []
    publishers = [("instagram_id", instagram_publisher)]
    if not instagram_only:
        if all(secret.get(key) for key in google_keys):
            publishers.append(("youtube_id", youtube))
        else:
            problems.append("YouTube publishing credentials incomplete")
    for item in sorted(items,''')
publish = edit(publish, '        if item.get("youtube_id") and item.get("instagram_id"):', '        if all(item.get(key) for key, _ in publishers):')
publish = edit(publish, '        for key, publish in (("youtube_id", youtube), ("instagram_id", instagram_publisher)):', '        for key, publish in publishers:')
publish = edit(publish, 'except (APIError, RuntimeError, KeyError, urllib.error.URLError) as error:', 'except (RuntimeError, KeyError, ValueError, OSError) as error:')
publish = edit(publish, 'def notify_verified_clip(item, secret):', 'def notify_verified_clip(item, secret, instagram_only=False):')
publish = edit(publish, '    if not (token and chat_id and item.get("youtube_id") and item.get("youtube_public_verified")', '''    youtube_ready = item.get("youtube_id") and item.get("youtube_public_verified")
    if not (token and chat_id and (instagram_only or youtube_ready)''')
publish = edit(publish, '        "✅ Published on YouTube and Instagram",', '        "✅ Published on Instagram" if instagram_only else "✅ Published on YouTube and Instagram",')
publish = edit(publish, '''    message.extend([
        f"YouTube: https://www.youtube.com/shorts/{item['youtube_id']}",
        f"Instagram: {item['instagram_url']}",
    ])''', '''    if not instagram_only:
        message.append(f"YouTube: https://www.youtube.com/shorts/{item['youtube_id']}")
    message.append(f"Instagram: {item['instagram_url']}")''')
publish = edit(publish, '    terminal = {"sent", "error", "failed"}', '''    terminal = {"sent", "error", "failed"}
    instagram_only = config.get("instagram_only", False)''')
publish = edit(publish, '            if video_id and config.get("youtube_privacy")', '            if not instagram_only and video_id and config.get("youtube_privacy")')
publish = edit(publish, '            (config.get("youtube_privacy") == "public"', '            (not instagram_only and config.get("youtube_privacy") == "public"')
publish = edit(publish, '            notify_verified_clip(item, secret)', '            notify_verified_clip(item, secret, instagram_only=instagram_only)')
for name, source in (("clips.py", clips), ("publish.py", publish)):
    compile(source, name, "exec")
for name, source in (("clips.py", clips), ("publish.py", publish)):
    target = Path(name)
    staged = target.with_suffix(".instagram-only.tmp")
    staged.write_text(source)
    staged.chmod(target.stat().st_mode & 0o777)
    os.replace(staged, target)
print("Instagram-only source patch installed")
