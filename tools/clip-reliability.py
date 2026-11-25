"""Patch the deployed clip worker in place without replacing its workflow."""
import os
from pathlib import Path


def change(source, old, new):
    if source.count(old) != 1:
        raise SystemExit(f"Unexpected worker version near {old[:70]!r}")
    return source.replace(old, new, 1)


clips = Path("clips.py").read_text()
publish = Path("publish.py").read_text()
if "# VPS reliability update" in clips and "# VPS reliability update" in publish:
    print("Reliability source update already installed")
    raise SystemExit(0)
clips = change(clips, "def out(*args):", '''# VPS reliability update
from publish import progress, telegram_message


def ytdlp():
    cookie = Path("/data/youtube-auth/youtube-cookies.txt")
    args = ["yt-dlp"]
    if cookie.is_file():
        args.extend(["--cookies", str(cookie)])
    return args


def out(*args):''')
clips = clips.replace('out("yt-dlp",', 'out(*ytdlp(),')
clips = change(clips, '["yt-dlp", "--no-cache-dir", "--js-runtimes", "node", "--no-playlist",',
               '[*ytdlp(), "--no-cache-dir", "--js-runtimes", "node", "--no-playlist",')
clips = change(clips, '                    subprocess.run(\n                        [*ytdlp()', '''                    progress(STATE, f"Downloading {key} at {start}s")
                    subprocess.run(
                        [*ytdlp()''')
clips = change(clips, 'check=True, timeout=300, stderr=subprocess.DEVNULL)',
               'check=True, timeout=300, stderr=subprocess.PIPE)')
old = '                print(f"Skipping unavailable source window {key} at {start}s", file=sys.stderr)'
clips = change(clips, old, '''                detail = getattr(error, "stderr", b"") or b""
                if b"confirm you" in detail.lower() and b"bot" in detail.lower():
                    raise PermissionError("YouTube rejected the scheduled session") from None
                progress(STATE, f"Source skipped: {key} at {start}s ({type(error).__name__})")''')
clips = change(clips, '            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):',
               '            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as error:')
clips = change(clips, '        render(sample, clip_start, video,', '        progress(STATE, f"Rendering {identifier}")\n        render(sample, clip_start, video,')
clips = change(clips, '        verification = []', '''        if not current:
            raise RuntimeError("No usable clips generated")
        verification = []''')
clips = change(clips, '            from publish import publish_pending, verify_publications',
               '            progress(STATE, f"Publishing {len(current)} clip(s) for {slot}")\n            from publish import publish_pending, verify_publications')
clips = change(clips, '                print(json.dumps({"date": date, "slot": slot, "verified": verification}))',
               '''                print(json.dumps({"date": date, "slot": slot, "verified": verification}))
            if config.get("youtube_privacy") == "public" and config.get("instagram_provider") == "buffer":
                if any(not item.get("youtube_public_verified") or item.get("buffer_post_status") != "sent"
                       for item in verification) or not verification:
                    raise RuntimeError("Publishing could not be verified on both platforms")
        progress(STATE, f"Finished {date} {slot}: {len(current)} clip(s)")''')
clips = change(clips, '    main()\n', '''    try:
        main()
    except Exception as error:
        if "--test" not in sys.argv and "--instagram-draft" not in sys.argv:
            reason = str(error) if str(error) in {
                "YouTube rejected the scheduled session", "No usable clips generated",
                "Publishing could not be verified on both platforms",
            } else type(error).__name__
            run_time = dt.datetime.now(INDIA)
            slot_index = sys.argv.index("--slot") + 1 if "--slot" in sys.argv else None
            slot = sys.argv[slot_index] if slot_index is not None and slot_index < len(sys.argv) else "automatic"
            message = f"Clip workflow failed | {run_time:%d %b %H:%M} IST | {slot} | {reason}"
            try:
                progress(STATE, message)
                credentials = json.loads((STATE / "credentials.json").read_text())
                telegram_message(credentials["telegram_bot_token"], credentials["telegram_chat_id"], message)
            except Exception as alert_error:
                print(f"Failure alert could not be sent: {type(alert_error).__name__}", file=sys.stderr)
        raise
''')
if 'except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as error:' not in clips:
    raise SystemExit("Source exception handler missing")
publish = change(publish, 'def telegram_message(token, chat_id, message):', '''# VPS reliability update
def progress(state, message):
    now = dt.datetime.now(ZoneInfo("Asia/Kolkata"))
    line = f"{now:%Y-%m-%d %H:%M:%S} IST | {message}"
    print(line, flush=True)
    with (state / "clip-progress.log").open("a") as log:
        log.write(line + "\\n")


def telegram_message(token, chat_id, message):''')
publish = change(publish, '                item[key] = publish(item, data, config, secret, save)',
                 '                progress(directory.parent, f"Uploading {item[\'id\']} to {\'YouTube\' if key == \'youtube_id\' else \'Instagram\'}")\n                item[key] = publish(item, data, config, secret, save)')
for name, source in (("clips.py", clips), ("publish.py", publish)):
    compile(source, name, "exec")
for name, source in (("clips.py", clips), ("publish.py", publish)):
    target = Path(name)
    staged = target.with_suffix(".reliability.tmp")
    staged.write_text(source)
    staged.chmod(target.stat().st_mode & 0o777)
    os.replace(staged, target)
print("Reliability source update installed")
