# Gaming clip automation on a VPS

This package ports the existing clip worker to Linux with Docker Compose. The scheduler starts one run daily at 17:00 Asia/Kolkata. Each run targets one new clip per configured creator, up to three clips. The worker targets 45 seconds, validates a 40–50 second duration, and exports 1080 × 1920 MP4s. Uploads can finish after 17:00 because fetching, rendering, and platform processing take time.

## Requirements

- Linux VPS with Docker Engine and the Docker Compose v2 plugin.
- SSH access and permission to create a project directory and run Docker.
- An initial 5–10 GB of free disk as a planning estimate. Measure Docker image, render scratch space, and exports on the actual VPS.
- Approved footage and working source URLs. Production publishing also needs YouTube Desktop OAuth credentials, a Buffer API key and connected Instagram account, and Cloudinary signed-upload credentials.

This project uses its own service and data directory. The dashboard maps to a loopback-only host port, 5678 by default. Your existing public app on port 8080 keeps its port.

## Unpack and configure

Transfer this ZIP over SSH, extract it into its own directory, and run the following commands from the extracted `clip-automation-vps` directory. These commands assume a root deployment shell. The worker container runs as UID/GID 1000, so the container must be able to read its configuration and write its data.

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
- `data/exports` holds finished MP4s, and temporary render samples are normally cleaned up after the run. Cloudinary hosts a copy for Buffer delivery. At 20–40 MB per clip and three clips per day, seven days of exports are roughly 420–840 MB, separate from Docker images, logs, and scratch space.
- Automatic retention is not configured. After verifying both posts, apply your retention policy to finished MP4s and hosted assets. Preserve queue records and keep pending uploads' files until you resolve them.
- If the image build fails, check server architecture, outbound registry access, disk space, and the pinned dependencies. If downloads fail, verify the current yt-dlp release and JS runtime, and check for accessible completed replays. If OAuth returns an authorization error, renew the refresh token. If Buffer is pending, check its delivery status before retrying. If the dashboard port is busy, change the host port in `.env` and recreate the service.
- A same-day retry of the same slot uses saved state and skips platforms with recorded post IDs. Reconcile an ambiguous upload in YouTube Studio or Buffer before retrying. The worker uses the current IST date, so running it tomorrow does not replay today's slot.
