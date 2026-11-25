"""Receive authorized Drive source links via the existing private Telegram bot."""
import datetime as dt
import json
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen


def drive_id(link):
    url = urlsplit(link)
    if url.scheme == "https" and url.netloc == "drive.google.com":
        match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]{10,128})(?:/view|/edit)?/?", url.path)
        if match:
            return match[1]
    raise ValueError("Use a Google Drive file link")


def download(file_id, key, target):
    params = urlencode({"alt": "media", "key": key})
    endpoint = f"https://www.googleapis.com/drive/v3/files/{file_id}?{params}"
    temp = target.with_suffix(".partial")
    limit = min(20 * 1024**3, shutil.disk_usage(target.parent).free - 2 * 1024**3)
    if limit <= 0:
        raise ValueError("Not enough free disk space for Drive footage")
    try:
        with urlopen(endpoint, timeout=45) as response, temp.open("wb") as output:
            if response.headers.get_content_type() in {"text/html", "application/json"}:
                raise ValueError("Drive did not return a video file")
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > limit:
                    raise ValueError("Drive video exceeds the intake limit or available disk space")
                output.write(chunk)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def run_series_window(job, secret, clips, publish):
    """Advance one source from the beginning without reusing published windows."""
    from clip_metadata import analyze

    state = json.loads(job.read_text())
    directory = job.parent
    queue = directory / "queue"
    queue.mkdir(exist_ok=True)
    credentials = directory / "credentials.json"
    if not credentials.exists():
        credentials.symlink_to(clips.STATE / "credentials.json")
    token, chat = secret["telegram_bot_token"], secret["telegram_chat_id"]
    source = directory / "recording.mp4"
    if not source.exists():
        download(state["file_id"], secret["drive_download_api_key"], source)
    length = int(clips.duration(source))
    start = state.get("next_start", 0)
    config = json.loads(clips.CONFIG.read_text())
    config["instagram_only"] = False
    today = dt.datetime.now(clips.INDIA).date().isoformat()
    summary = "No suitable window in this slot."
    posted = False

    # Bound model calls so quiet sections cannot monopolize the scheduled worker.
    for _ in range(3):
        end = start + clips.DURATION
        if end > length:
            break
        record = queue / f"drive-{state['file_id']}-{start}.json"
        if record.exists():
            item = json.loads(record.read_text())
        else:
            result = analyze(source, start, clips.DURATION, secret["gemini_api_key"])
            if result is None:
                start = end
                state["next_start"] = start
                clips.write_json(job, state)
                continue
            _, metadata = result
            output = directory / f"clip-{start}.mp4"
            clips.render(source, start, output, False)
            item = dict(metadata, id=record.stem, date=today, slot="telegram",
                        start=start, end=end, path=str(output),
                        youtube_id=None, instagram_id=None)
            clips.write_json(record, item)
        item["date"] = today
        try:
            publish.publish_pending([item], queue, config, clips.write_json, today)
        except Exception as error:
            publish.progress(clips.STATE, f"Drive job {directory.name} publish: {type(error).__name__}")
        publish.verify_publications([item], queue, config, clips.write_json)
        yt = bool(item.get("youtube_id") and item.get("youtube_public_verified"))
        ig = bool(item.get("buffer_post_status") == "sent" and item.get("instagram_url"))
        window = f"{dt.timedelta(seconds=start)}–{dt.timedelta(seconds=end)}"
        youtube = "https://www.youtube.com/shorts/" + item["youtube_id"] if yt else "not verified"
        instagram = item["instagram_url"] if ig else "not verified"
        summary = f"{window}\nYouTube: {youtube}\nInstagram: {instagram}"
        if not (yt or ig):
            state.update(status="held", summary=summary)
            clips.write_json(job, state)
            publish.telegram_message(token, chat, f"Drive job {directory.name} held.\n{summary}")
            return "held"
        posted = True
        start = end
        break

    exhausted = start + clips.DURATION > length
    state.update(status="complete" if exhausted else "scheduled", next_start=start,
                 summary=summary + f"\nProgress: {dt.timedelta(seconds=start)} of {dt.timedelta(seconds=length)}")
    clips.write_json(job, state)
    if posted and yt and ig:
        Path(item["path"]).unlink(missing_ok=True)
    if exhausted:
        source.unlink(missing_ok=True)
        message = f"Drive job {directory.name}: reached the end of the usable footage. Send a new /clip link."
    else:
        message = f"Drive job {directory.name}: next slot resumes at {dt.timedelta(seconds=start)}."
    publish.telegram_message(token, chat, message + "\n" + state["summary"])
    return "complete" if posted else "no_clip"


def drive_slot_open(now, date, slot):
    return (now.date().isoformat() == date
            and now.strftime("%H:%M") == {"morning": "08:45", "evening": "16:45"}.get(slot))


def run_scheduled_slot(clips, date, slot, capacity=3):
    """Reserve a different source for each available clip in this slot."""
    import publish

    if not drive_slot_open(dt.datetime.now(clips.INDIA), date, slot):
        return {}
    root = clips.STATE / "telegram-drive"
    if not root.is_dir():
        return {}
    slot_file = root / f"slot-{date}-{slot}.json"
    if slot_file.exists():
        reservation = json.loads(slot_file.read_text())
        if "job_id" in reservation:
            # A slot created by the single-source worker already ran.
            job_id = reservation["job_id"]
            job = root / job_id / "job.json"
            return {job_id: reservation.get("result", json.loads(job.read_text())["status"])}
    else:
        jobs = sorted(root.glob("*/job.json"), key=lambda job: int(job.parent.name))
        selected = []
        seen = set()
        for job in jobs:
            state = json.loads(job.read_text())
            source_id = state["file_id"]
            if state["status"] in {"scheduled", "queued"} and source_id not in seen:
                selected.append(job.parent.name)
                seen.add(source_id)
                if len(selected) == capacity:
                    break
        if not selected:
            return {}
        reservation = {"jobs": selected, "results": {}}
        clips.write_json(slot_file, reservation)

    results = reservation["results"]
    for job_id in reservation["jobs"]:
        if job_id in results:
            continue
        job = root / job_id / "job.json"
        state = json.loads(job.read_text())
        if state["status"] not in {"scheduled", "queued"} or state.get("slot") == f"{date} {slot}":
            results[job_id] = state["status"]
            clips.write_json(slot_file, reservation)
            continue
        secret = json.loads((clips.STATE / "credentials.json").read_text())
        state.update(status="processing", slot=f"{date} {slot}")
        clips.write_json(job, state)
        publish.progress(clips.STATE, f"Scheduled Drive job {job_id}: {date} {slot}")
        try:
            results[job_id] = run_series_window(job, secret, clips, publish)
        except Exception as error:
            state = json.loads(job.read_text())
            results[job_id] = state["status"]
            if state["status"] == "processing":
                failure = type(error).__name__
                if getattr(error, "code", None):
                    failure += f" HTTP {error.code}"
                state.update(status="held", summary=failure)
                clips.write_json(job, state)
                results[job_id] = "held"
                try:
                    publish.telegram_message(secret["telegram_bot_token"], secret["telegram_chat_id"],
                                             f"Scheduled Drive job {job_id} failed: {failure}")
                except (OSError, RuntimeError, ValueError, KeyError) as alert_error:
                    publish.progress(clips.STATE, f"Drive alert failed: {type(alert_error).__name__}")
        clips.write_json(slot_file, reservation)
    return results


def main():
    sys.path.insert(0, "/opt/clips")
    import clips
    import publish

    root = clips.STATE / "telegram-drive"
    root.mkdir(mode=0o700, exist_ok=True)
    offset_file = root / "telegram-offset.json"
    secret = json.loads((clips.STATE / "credentials.json").read_text())
    token, chat = secret["telegram_bot_token"], int(secret["telegram_chat_id"])
    if not secret.get("drive_download_api_key"):
        raise SystemExit("Set drive_download_api_key before starting Drive intake")
    # Ignore messages from before the first installation.
    offset = json.loads(offset_file.read_text()) if offset_file.exists() else -1
    while True:
        try:
            if offset == -1:
                _, _, body = publish.request(
                    f"https://api.telegram.org/bot{token}/getUpdates", "POST",
                    payload={"offset": -1, "timeout": 0},
                )
                offset = max((update["update_id"] + 1 for update in body["result"]), default=0)
                clips.write_json(offset_file, offset)

            _, _, body = publish.request(
                f"https://api.telegram.org/bot{token}/getUpdates", "POST",
                payload={"offset": offset, "timeout": 15, "allowed_updates": ["message"]},
            )
            for update in body["result"]:
                message = update.get("message", {})
                if message.get("chat", {}).get("id") == chat and message.get("chat", {}).get("type") == "private":
                    text = message.get("text", "").strip()
                    command, _, value = text.partition(" ")
                    if command == "/clip":
                        try:
                            file_id = drive_id(value.strip())
                        except ValueError:
                            publish.telegram_message(token, chat, "Send /clip followed by a Google Drive file link.")
                        else:
                            directory = root / str(update["update_id"])
                            job = directory / "job.json"
                            duplicate = False
                            for existing in root.glob("*/job.json"):
                                prior = json.loads(existing.read_text())
                                if prior["file_id"] == file_id and prior["status"] in {"queued", "scheduled", "processing", "held"}:
                                    duplicate = True
                                    publish.telegram_message(token, chat,
                                                             f"That Drive link already has job {existing.parent.name}. Send /status.")
                                    break
                            if not duplicate and not job.exists():
                                directory.mkdir(exist_ok=True)
                                clips.write_json(job, {"file_id": file_id, "status": "scheduled"})
                                publish.telegram_message(token, chat,
                                                         f"Drive job {directory.name} scheduled for 8:45 AM and 4:45 PM IST. "
                                                         "Send /status to check progress.")
                    elif command == "/now":
                        publish.telegram_message(token, chat, "Drive uploads run only at 8:45 AM and 4:45 PM IST. Use /clip to queue a link.")
                    elif command == "/status":
                        jobs = sorted(root.glob("*/job.json"), key=lambda job: int(job.parent.name))[-5:]
                        lines = []
                        for job in jobs:
                            state = json.loads(job.read_text())
                            lines.append(f"{job.parent.name}: {state['status']}\n{state.get('summary', '')}")
                        publish.telegram_message(token, chat, "\n".join(lines) or "No Drive jobs yet.")
                    elif command == "/start":
                        publish.telegram_message(token, chat, "Send /clip <Google Drive file link> for the scheduled slots or /status to check progress.")
                offset = update["update_id"] + 1
                clips.write_json(offset_file, offset)
        except Exception as error:
            # Never log request URLs, which can contain API keys.
            print(f"Telegram Drive intake: {type(error).__name__}", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
