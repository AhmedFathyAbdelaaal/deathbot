#!/bin/sh
# Render the SPA's runtime config from container env vars (Coolify-injected),
# so API_BASE_URL / WS_BASE_URL drive the frontend without a rebuild.
set -e

: "${API_BASE_URL:=https://api.deathbot.captionato.tech}"
: "${WS_BASE_URL:=wss://api.deathbot.captionato.tech}"

cat > /usr/share/nginx/html/assets/config.json <<EOF
{
  "apiBaseUrl": "${API_BASE_URL}",
  "wsBaseUrl": "${WS_BASE_URL}"
}
EOF

echo "[deathbot] runtime config: API=${API_BASE_URL} WS=${WS_BASE_URL}"
