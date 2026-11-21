FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg python3 python3-venv make g++ tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/clips-python \
    && /opt/clips-python/bin/pip install --no-cache-dir 'yt-dlp[default]==2026.10.26'
RUN npm install --prefix /opt/n8n --ignore-scripts --no-audit --no-fund n8n@2.48.5 \
    && npm --prefix /opt/n8n rebuild sqlite3
ENV PATH="/opt/n8n/node_modules/.bin:/opt/clips-python/bin:${PATH}"
WORKDIR /opt/clips
COPY clips.py publish.py buffer_reels.py daily-workflow.json ./
COPY entrypoint.sh /usr/local/bin/start-clips
RUN chmod 755 /usr/local/bin/start-clips
USER node
ENTRYPOINT ["/usr/local/bin/start-clips"]
