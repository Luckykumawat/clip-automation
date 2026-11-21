"""Publish a local vertical MP4 through Cloudinary and Buffer."""

import hashlib
import re
import time
import uuid
from urllib.parse import urlsplit

from publish import APIError, request


def graphql(key, query, variables=None):
    _, _, body = request(
        "https://api.buffer.com", "POST",
        headers={"Authorization": f"Bearer {key}"},
        payload={"query": query, "variables": variables or {}},
    )
    if body.get("errors") or not isinstance(body.get("data"), dict):
        raise RuntimeError("Buffer GraphQL did not return data")
    return body["data"]


def find_channel(key, handle):
    organizations = graphql(key, "{ account { organizations { id } } }")["account"]["organizations"]
    matches = []
    for org in organizations:
        organization_id = org["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", organization_id):
            raise RuntimeError("Buffer returned an invalid organization ID")
        query = ('{ channels(input: { organizationId: "' + organization_id + '" }) '
                 '{ id name service isDisconnected } }')
        for channel in graphql(key, query)["channels"]:
            if (channel["service"] == "instagram" and not channel["isDisconnected"]
                    and channel["name"].lstrip("@").casefold() == handle.lstrip("@").casefold()):
                matches.append(channel["id"])
    if len(matches) != 1:
        raise RuntimeError(f"Expected one connected Buffer Instagram channel for {handle}")
    return matches[0]


def host_video(data, secret, identifier):
    cloud = secret["cloudinary_cloud_name"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cloud):
        raise ValueError("Invalid Cloudinary cloud name")
    asset_id = hashlib.sha256(identifier.encode() + data).hexdigest()[:36]
    fields = {"overwrite": "false", "public_id": f"daily-vertical-clips/{asset_id}",
              "timestamp": str(int(time.time()))}
    signature_text = "&".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["signature"] = hashlib.sha256(
        (signature_text + secret["cloudinary_api_secret"]).encode()).hexdigest()
    fields["api_key"] = secret["cloudinary_api_key"]
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.extend([
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
         'filename="clip.mp4"\r\nContent-Type: video/mp4\r\n\r\n').encode(),
        data, f"\r\n--{boundary}--\r\n".encode(),
    ])
    result = request(
        f"https://api.cloudinary.com/v1_1/{cloud}/video/upload", "POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        raw=b"".join(parts),
    )[2]
    url = result["secure_url"]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "res.cloudinary.com":
        raise RuntimeError("Cloudinary did not provide its public HTTPS delivery URL")
    return url


def create_reel(item, data, config, secret, save, *, draft=False):
    if item.get("buffer_submitting"):
        raise RuntimeError("Prior Buffer response was uncertain. Check Buffer before retrying.")
    if item.get("buffer_post_id"):
        return item["buffer_post_id"]
    key = secret["buffer_api_key"]
    channel = find_channel(key, config["instagram_handle"])
    if not item.get("buffer_video_url"):
        item["buffer_video_url"] = host_video(data, secret, item["id"])
        save()
    variables = {"input": {
        "channelId": channel, "text": item["caption"],
        "assets": [{"video": {"url": item["buffer_video_url"]}}],
        "metadata": {"instagram": {"type": "reel", "shouldShareToFeed": True}},
        "schedulingType": "automatic", "mode": "addToQueue" if draft else "shareNow",
        "needsApproval": False, "saveToDraft": draft,
    }}
    item["buffer_submitting"] = True
    save()
    try:
        result = graphql(key, """
            mutation CreateReel($input: CreatePostInput!) {
              createPost(input: $input) {
                ... on PostActionSuccess { post { id status } }
                ... on MutationError { message }
              }
            }""", variables)["createPost"]
    except APIError as exc:
        if exc.status in (400, 401, 403, 404, 422, 429):
            item.pop("buffer_submitting", None)
            save()
        raise
    if "post" not in result:
        item.pop("buffer_submitting", None)
        save()
        raise RuntimeError("Buffer rejected the Reel. Check the channel and media settings.")
    post = result["post"]
    item["buffer_post_id"] = post["id"]
    item["buffer_post_status"] = post["status"]
    item.pop("buffer_submitting")
    save()
    return post["id"]
