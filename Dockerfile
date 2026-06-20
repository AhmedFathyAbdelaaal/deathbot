FROM python:3.11-slim

# Install FFmpeg for audio streaming
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install Deno — required by yt-dlp to solve YouTube's signature/n-function
# challenges as of 2026. Without this, yt-dlp falls back to weaker player
# clients and stale formats (e.g. itag=18) that are increasingly blocked
# by YouTube's SABR enforcement.
RUN apt-get update && apt-get install -y unzip curl && \
    curl -fsSL https://deno.land/install.sh | sh && \
    mv /root/.deno/bin/deno /usr/local/bin/deno && \
    apt-get remove -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
