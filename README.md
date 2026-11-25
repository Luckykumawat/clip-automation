# Gaming clip automation on a VPS

This package ports the existing clip worker to Linux with Docker Compose. The n8n workflow starts twice daily at 08:45 and 16:45 Asia/Kolkata. Each slot targets up to three 45-second vertical clips at 1080 × 1920. Posting and platform verification can finish after the trigger time.

## Requirements

- Linux VPS with Docker Engine and the Docker Compose v2 plugin.
- SSH access and permission to create a project directory and run Docker.
- An initial 5–10 GB of free disk as a planning estimate. Measure Docker image, render scratch space, and exports on the actual VPS.
- Approved footage and working source URLs. Production publishing also needs YouTube Desktop OAuth credentials, a Buffer API key and connected Instagram account, and Cloudinary signed-upload credentials.

This project uses its own service and data directory. The dashboard maps to a loopback-only host port, 5678 by default. Your existing public app on port 8080 keeps its port.

## Unpack and configure

Clone the repository on the VPS and run the following commands from its deployment directory. The maintenance scripts expect the VPS checkout at `/opt/clip-automation-vps`. These commands assume a root deployment shell. The worker container runs as UID/GID 1000, so the container must be able to read its configuration and write its data.

```sh
docker --version
docker compose version
df -h .
ss -ltn
cp .env.example .env
install -d -m 750 -o 1000 -g 1000 data data/state data/exports data/recordings
install -m 600 -o 1000 -g 1000 config.example.json data/config.json
docker compose config -q
docker compose build
```

If 5678 is occupied, set `CLIPS_DASHBOARD_PORT` in `.env` to a free host port and use that port in the commands below. Review the three source URLs in `data/config.json`. Leave `auto_publish` set to `false` during testing. If your editor replaces the file, recheck its ownership and permissions.

## Run an isolated test

This run uses the configured replay sources and fresh test directories inside the temporary container. It leaves the production queue untouched. The output is a JSON summary. Check that the expected creators produced clips. A blocked source or a shortage of eligible windows can yield fewer than three.

```sh
docker compose run --rm --entrypoint env clips \
  CLIPS_STATE_DIR=/tmp/clip-smoke-state \
  CLIPS_EXPORT_DIR=/tmp/clip-smoke-exports \
  CLIPS_RECORDINGS_DIR=/tmp/clip-smoke-recordings \
  python3 /opt/clips/clips.py --slot evening
```

Initialize the workflow once on a fresh data directory, then start the service.

```sh
docker compose run --rm clips init
docker compose up -d
docker compose ps
docker compose logs --tail=80 clips
curl -fsS http://127.0.0.1:5678/healthz/readiness
```

Check the startup log for the scheduler and published workflow. Open an SSH tunnel from your computer and go to `http://localhost:15678` to inspect the schedule and execution history. Replace the VPS login and host port as needed.

```sh
ssh -N -L 15678:127.0.0.1:5678 USER@VPS
```

## Configure publishing and switch from the Mac

1. Create the VPS credentials file from the template with the command below. Fill it on the server or transfer the populated file privately over SSH. Use Google OAuth client ID, client secret and offline refresh token for the intended YouTube channel. Use the Buffer API key and Cloudinary cloud name, API key and API secret. Leave the direct Meta fields blank for the Buffer provider. Set your actual Instagram handle in `data/config.json`.
2. Stop the Mac scheduler and let active uploads finish. Check YouTube Studio and Buffer for pending or ambiguous uploads. Transfer current queue records privately into `data/state/queue` to preserve source-window history and post IDs. Transfer corresponding MP4s for any entries you plan to resume or retain into `data/exports`, using the original clip ID as the filename. Review unfinished slots explicitly during the cutover. Verify destination ownership after any transfer.
3. Set `auto_publish` to `true` only when you are ready for public posting. Prefer starting with the next unused slot. Avoid manually triggering the same slot on both machines. The worker reads config on each run.

```sh
install -m 600 -o 1000 -g 1000 credentials.example.json data/state/credentials.json
```

Useful commands and references

- Manual evening run after reviewing the publishing setting — `docker compose exec clips python3 /opt/clips/clips.py --slot evening`
- Pause scheduling on the VPS — `docker compose stop clips`
- Follow service logs — `docker compose logs -f clips`
- Google OAuth setup — https://developers.google.com/youtube/data/guides/uploading_a_video
- Buffer video publishing — https://developers.buffer.com/examples/create-video-post.html
- Cloudinary signed upload — https://cloudinary.com/documentation/upload_parameters

## Storage and failures

- `data/n8n` holds the scheduler's SQLite database and execution history. `data/state` holds queue records, source windows, publication IDs, and the private credentials file. A standalone database server is unnecessary.
- `data/exports` holds finished MP4s, and temporary render samples are normally cleaned up after the run. Cloudinary hosts a copy for Buffer delivery. At 20–40 MB per clip and six clips per day, seven days of exports are roughly 840–1,680 MB, separate from Docker images, logs, and scratch space.
- Automatic retention is not configured. After verifying both posts, apply your retention policy to finished MP4s and hosted assets. Preserve queue records and keep pending uploads' files until you resolve them.
- If the image build fails, check server architecture, outbound registry access, disk space, and the pinned dependencies. If downloads fail, verify the current yt-dlp release and JS runtime, and check for accessible completed replays. If OAuth returns an authorization error, renew the refresh token. If Buffer is pending, check its delivery status before retrying. If the dashboard port is busy, change the host port in `.env` and recreate the service.
- A same-day retry of the same slot uses saved state and skips platforms with recorded post IDs. Reconcile an ambiguous upload in YouTube Studio or Buffer before retrying. The worker uses the current IST date, so running it tomorrow does not replay today's slot.

## Clip-specific titles and captions

Set `gemini_api_key` in `data/state/credentials.json` (mode 600) and enable
`content_aware_clips` in `data/config.json`. This sends compressed candidate
footage and audio to Google's Gemini API. The worker rejects weak candidates
and ranks the rest by the opening hook, clarity, and payoff. It saves separate
YouTube and Instagram copy. Older queued clips with generic copy are held
when this feature is enabled.

Rebuild the worker with `docker compose up -d --build` from the deployment
directory. To preview generated copy without publishing, run `clips.py --test`
with a local clip in the container. The preview uses the same video analysis
and writes only to the test directory.


## Source and publishing modes

- Set `channel_streams_urls` to creator Live tabs and optionally add individual
  YouTube watch links to `youtube_video_urls`. An individual URL gets priority
  until its first clip is queued. Creator feeds can then fill unused slots.
- For YouTube sources, the worker uses the cookie file at
  `data/youtube-auth/youtube-cookies.txt` when it exists. Keep it private and
  readable by the worker. The bind-mounted file survives container rebuilds.
  Refresh it when YouTube rejects the session.
- Use `instagram_only: true` to publish Instagram Reels from authorized files
  in `data/recordings` without fetching YouTube sources. In dual-platform mode,
  Instagram publishing is attempted first, and its saved result survives a
  YouTube publishing error.
- Progress is written to `data/state/clip-progress.log`. Configure
  `telegram_bot_token` and `telegram_chat_id` for publication and failure alerts.

## Telegram Drive scheduling

Run the following from the local checkout after setting the VPS SSH login.
The first installer prompts privately for a Google Drive API key if needed.
The second installer enables the scheduled Drive intake and updates the running
clip worker without recreating its container.

```sh
export CLIPS_SSH_HOST="root@YOUR_VPS"
bash tools/install-telegram-drive.sh
bash tools/install-scheduled-drive.sh
```

Send `/status` in the configured private bot chat to verify it responds.
Submit each downloadable Drive file with `/clip <Drive file link>`. The next
08:45 or 16:45 IST slot takes up to three distinct scheduled Drive sources,
one clip per source. Each source saves its next timestamp and continues in
subsequent slots until less than 45 seconds of usable footage remains.
The bot reports progress, platform links, and when a new source is needed.
Use `/now <Drive file link>` for an immediate one-off publishing request.

Use footage you have permission to repost. With API-key access the Drive
file must be downloadable by anyone with its link. Intake is capped at
20 GB and may be lower based on available disk space. Use `/status` to
check held jobs before submitting the same file again. The separate,
optional Telegram creator-selection setting should stay disabled when
the Drive listener uses the same bot token.

## Mac commands and troubleshooting

Open the [offline HTML guide](clip-workflow-guide.html) locally for copyable
commands, Drive sharing steps, and the full publishing checklist.
Run its commands from the repository root. Set `CLIPS_HISTORY_DIR` if old
local timestamp records are stored outside this checkout.

```sh
bash tools/cut-three-to-desktop.sh
bash tools/clip-cookie-refresh.sh refresh
```

The first command exports cuts from sources absent in the available upload
records. The refresh command tests a fresh `~/Downloads/youtube-cookies.txt`
before replacing the scheduled cookie file. Use `morning` or `evening` in
place of `refresh` to retry that slot on the current IST date.

This repository contains source, templates, and maintenance tools. Keep
credentials, cookie files, production data, and rendered media on the VPS
or in their private local locations.
