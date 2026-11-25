import datetime as dt
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


class APIError(RuntimeError):
    def __init__(self, status):
        super().__init__(f"Publishing API returned HTTP {status}")
        self.status = status


def request(url, method="GET", *, headers=None, payload=None, raw=None, allowed=()):
    headers = dict(headers or {})
    if payload is not None:
        raw = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            data = response.read()
            return response.status, response.headers, json.loads(data) if data else {}
    except urllib.error.HTTPError as error:
        if error.code in allowed:
            return error.code, error.headers, {}
        raise APIError(error.code) from None


def google_token(secret):
    data = urllib.parse.urlencode({
        "client_id": secret["google_client_id"],
        "client_secret": secret["google_client_secret"],
        "refresh_token": secret["google_refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    return request("https://oauth2.googleapis.com/token", "POST",
                   headers={"Content-Type": "application/x-www-form-urlencoded"},
                   raw=data)[2]["access_token"]


def youtube(item, data, config, secret, save):
    token = google_token(secret)
    auth = {"Authorization": f"Bearer {token}"}
    session = item.get("youtube_session")
    size = len(data)
    if session:
        status, headers, result = request(
            session, "PUT", headers={**auth, "Content-Range": f"bytes */{size}"},
            raw=b"", allowed=(308, 404, 410))
        if result.get("id"):
            item["youtube_reported_privacy"] = result.get("status", {}).get("privacyStatus")
            return result["id"]
        if status in (404, 410):
            raise RuntimeError("Expired YouTube upload session. Verify in Studio before retrying.")
        range_value = headers.get("Range")
        offset = int(range_value.split("-")[-1]) + 1 if range_value else 0
    else:
        _, headers, _ = request(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status", "POST",
            headers={**auth, "X-Upload-Content-Length": str(size),
                     "X-Upload-Content-Type": "video/mp4"},
            payload={"snippet": {"title": item["title"], "description": item.get("youtube_description", item["caption"]),
                                 "categoryId": "20"},
                     "status": {"privacyStatus": config["youtube_privacy"],
                                "selfDeclaredMadeForKids": False}})
        session = headers["Location"]
        item["youtube_session"] = session
        save()
        offset = 0
    result = request(
        session, "PUT", headers={**auth, "Content-Type": "video/mp4",
                                 "Content-Range": f"bytes {offset}-{size - 1}/{size}"},
        raw=data[offset:])[2]
    item["youtube_reported_privacy"] = result.get("status", {}).get("privacyStatus")
    return result["id"]


def instagram(item, data, config, secret, save):
    version = config["meta_graph_version"]
    account = secret["instagram_account_id"]
    if not re.fullmatch(r"v\d+\.\d+", version) or not re.fullmatch(r"\d+", account):
        raise ValueError("Invalid Meta API version or account ID")
    graph = f"https://graph.facebook.com/{version}"
    token = secret["facebook_page_access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    container = item.get("instagram_container")
    if not container:
        container = request(
            f"{graph}/{account}/media", "POST", headers=auth,
            payload={"media_type": "REELS", "upload_type": "resumable",
                     "caption": item["caption"], "share_to_feed": True})[2]["id"]
        item["instagram_container"] = container
        save()
    endpoint = f"{graph}/{container}?fields=status_code"
    status = request(endpoint, headers=auth)[2].get("status_code")
    if status == "PUBLISHED":
        return f"published-container:{container}"
    if status not in ("FINISHED", "ERROR", "EXPIRED") and not item.get("instagram_uploaded"):
        result = request(
            f"https://rupload.facebook.com/ig-api-upload/{version}/{container}", "POST",
            headers={"Authorization": f"OAuth {token}", "offset": "0",
                     "file_size": str(len(data)), "Content-Type": "application/octet-stream"},
            raw=data)[2]
        if not result.get("success"):
            raise RuntimeError("Instagram did not accept the video upload")
        item["instagram_uploaded"] = True
        save()
    for _ in range(36):
        status = request(endpoint, headers=auth)[2].get("status_code")
        if status == "FINISHED":
            return request(f"{graph}/{account}/media_publish", "POST", headers=auth,
                           payload={"creation_id": container})[2]["id"]
        if status == "PUBLISHED":
            return f"published-container:{container}"
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container {status}")
        time.sleep(5)
    raise RuntimeError("Instagram is still processing the Reel")


def publish_pending(items, directory, config, write_json, date):
    secret_file = directory.parent / "credentials.json"
    if not secret_file.is_file() or secret_file.stat().st_mode & 0o077:
        raise RuntimeError("Create work/n8n-clips-state/credentials.json with file mode 600 before publishing")
    secret = json.loads(secret_file.read_text())
    # Instagram-only publishing
    instagram_only = config.get("instagram_only", False)
    google_keys = {"google_client_id", "google_client_secret", "google_refresh_token"}
    keys = set()
    if config.get("instagram_provider") == "buffer":
        from buffer_reels import create_reel
        instagram_publisher = create_reel
        keys.update({"buffer_api_key", "cloudinary_cloud_name", "cloudinary_api_key",
                     "cloudinary_api_secret"})
    else:
        instagram_publisher = instagram
        keys.update({"instagram_account_id", "facebook_page_access_token"})
    if any(not secret.get(key) for key in keys):
        raise RuntimeError("Complete YouTube and Instagram publishing credentials first")
    problems = []
    publishers = [("instagram_id", instagram_publisher)]
    if not instagram_only:
        if all(secret.get(key) for key in google_keys):
            publishers.append(("youtube_id", youtube))
        else:
            problems.append("YouTube publishing credentials incomplete")
    for item in sorted(items, key=lambda x: (x["date"], x["start"])):
        if item.get("manually_published"):
            continue
        if item["date"] != date and not (item.get("youtube_session") or item.get("instagram_container") or item.get("buffer_video_url")):
            continue
        if all(item.get(key) for key, _ in publishers):
            continue
        data = Path(item["path"]).read_bytes()

        def save():
            write_json(directory / (item["id"] + ".json"), item)

        for key, publish in publishers:
            if item.get(key):
                continue
            try:
                progress(directory.parent, f"Uploading {item['id']} to {'YouTube' if key == 'youtube_id' else 'Instagram'}")
                item[key] = publish(item, data, config, secret, save)
                save()
            except (RuntimeError, KeyError, ValueError, OSError) as error:
                problems.append(f"{item['id']} {key}: {type(error).__name__}"
                                + (f" HTTP {error.status}" if isinstance(error, APIError) else ""))
    if problems:
        raise RuntimeError("; ".join(problems))


# VPS reliability update
def progress(state, message):
    now = dt.datetime.now(ZoneInfo("Asia/Kolkata"))
    line = f"{now:%Y-%m-%d %H:%M:%S} IST | {message}"
    print(line, flush=True)
    with (state / "clip-progress.log").open("a") as log:
        log.write(line + "\n")


def telegram_message(token, chat_id, message):
    _, _, body = request(
        f"https://api.telegram.org/bot{token}/sendMessage", "POST",
        payload={"chat_id": chat_id, "text": message,
                 "link_preview_options": {"is_disabled": True}},
    )
    return body["result"]["message_id"]


def choose_creators(channels, queue, date, slot, write_json):
    if len(channels) != 5 or len(set(channels)) != 5:
        raise ValueError("Telegram creator selection requires five distinct channel URLs")
    secret = json.loads((queue.parent / "credentials.json").read_text())
    token, chat_id = secret["telegram_bot_token"], int(secret["telegram_chat_id"])
    state_path = queue.parent / f"creator-selection-{date}-{slot}.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if "selected" in state:
        return state["selected"]
    if not state:
        options = "\n".join(f"{index}. {channel.split('/')[3]}"
                            for index, channel in enumerate(channels, 1))
        sent_at = time.time()
        try:
            message_id = telegram_message(
                token, chat_id,
                f"Choose creators for {date} ({slot}, IST)\n\n{options}\n\n"
                "Reply directly to this message with 1 to 3 numbers, for example 1, 3, 5. "
                "If there's no valid reply in 10 minutes, three creators will be picked randomly.",
            )
        except (APIError, ValueError, KeyError, urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(f"Could not send creator selection: {type(error).__name__}") from None
        state = {"message_id": message_id, "deadline": sent_at + 600, "channels": channels}
        write_json(state_path, state)
    channels = state["channels"]
    offset = None
    while (remaining := state["deadline"] - time.time()) > 0:
        params = {"timeout": min(15, int(remaining)), "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        try:
            _, _, body = request(f"https://api.telegram.org/bot{token}/getUpdates",
                                 "POST", payload=params)
        except (APIError, ValueError, urllib.error.URLError, TimeoutError):
            time.sleep(min(5, remaining))
            continue
        for update in body.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message", {})
            if (message.get("chat", {}).get("id") != chat_id
                    or message.get("reply_to_message", {}).get("message_id") != state["message_id"]
                    or message.get("date", 0) > state["deadline"]):
                continue
            choices = message.get("text", "").strip()
            if not re.fullmatch(r"[1-5](?:[ ,]+[1-5]){0,2}", choices):
                continue
            numbers = list(dict.fromkeys(map(int, re.findall(r"[1-5]", choices))))
            state["selected"] = [channels[number - 1] for number in numbers]
            break
        if "selected" in state:
            break
    if "selected" not in state:
        state["selected"] = random.sample(channels, 3)
    write_json(state_path, state)
    return state["selected"]


def notify_verified_clip(item, secret, instagram_only=False):
    token = secret.get("telegram_bot_token")
    chat_id = secret.get("telegram_chat_id")
    youtube_ready = item.get("youtube_id") and item.get("youtube_public_verified")
    if not (token and chat_id and (instagram_only or youtube_ready)
            and item.get("buffer_post_status") == "sent" and item.get("instagram_url")
            and not item.get("telegram_message_id")):
        return
    now = dt.datetime.now(ZoneInfo("Asia/Kolkata"))
    verified_at = item.setdefault("publish_confirmed_at", now.isoformat())
    start, end = (str(dt.timedelta(seconds=item[key])) for key in ("start", "end"))
    source = item.get("source_url")
    message = [
        "✅ Published on Instagram" if instagram_only else "✅ Published on YouTube and Instagram",
        item["title"],
        f"Source clip: {start}–{end}",
        f"Publish confirmed: {dt.datetime.fromisoformat(verified_at):%d %b, %I:%M %p} IST",
    ]
    if source:
        separator = "&" if "?" in source else "?"
        message.append(f"Source: {source}{separator}t={item['start']}s")
    if not instagram_only:
        message.append(f"YouTube: https://www.youtube.com/shorts/{item['youtube_id']}")
    message.append(f"Instagram: {item['instagram_url']}")
    try:
        item["telegram_message_id"] = telegram_message(token, chat_id, "\n".join(message))
    except (RuntimeError, ValueError, KeyError, urllib.error.URLError, TimeoutError) as error:
        print(f"Telegram alert failed for {item['id']}: {type(error).__name__}", file=sys.stderr)


def verify_publications(items, directory, config, write_json):
    """Verify public YouTube visibility and Buffer's delivery to Instagram."""
    from buffer_reels import graphql

    pending = [item for item in items if not item.get("manually_published")]
    if not pending:
        return []
    secret = json.loads((directory.parent / "credentials.json").read_text())
    query = """query VerifyReel($input: PostInput!) {
        post(input: $input) { status externalLink }
    }"""
    terminal = {"sent", "error", "failed"}
    instagram_only = config.get("instagram_only", False)
    for attempt in range(12):
        for item in pending:
            video_id = item.get("youtube_id")
            if not instagram_only and video_id and config.get("youtube_privacy") == "public" and not item.get("youtube_public_verified"):
                url = "https://www.youtube.com/watch?v=" + video_id
                params = urllib.parse.urlencode({"url": url, "format": "json"})
                try:
                    _, _, body = request("https://www.youtube.com/oembed?" + params)
                    item["youtube_public_verified"] = bool(body.get("title"))
                except (APIError, ValueError, urllib.error.URLError):
                    item["youtube_public_verified"] = False
            buffer_id = item.get("instagram_id")
            if (buffer_id and config.get("instagram_provider") == "buffer"
                    and (item.get("buffer_post_status") not in terminal
                         or item.get("buffer_post_status") == "sent" and not item.get("instagram_url"))):
                try:
                    post = graphql(secret["buffer_api_key"], query, {"input": {"id": buffer_id}})["post"]
                    item["buffer_post_status"] = post["status"]
                    item["instagram_url"] = post.get("externalLink")
                except (RuntimeError, ValueError, KeyError, urllib.error.URLError):
                    pass
            notify_verified_clip(item, secret, instagram_only=instagram_only)
            write_json(directory / (item["id"] + ".json"), item)
        waiting = any(
            (not instagram_only and config.get("youtube_privacy") == "public" and item.get("youtube_id")
             and not item.get("youtube_public_verified"))
            or (item.get("instagram_id") and config.get("instagram_provider") == "buffer"
                and item.get("buffer_post_status") not in terminal)
            for item in pending)
        if not waiting or attempt == 11:
            break
        time.sleep(8)
    return [{key: item.get(key) for key in (
        "title", "creator", "youtube_id", "youtube_public_verified",
        "buffer_post_status", "instagram_url")} for item in pending]
