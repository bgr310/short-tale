# ---------------------------------------------------------------------------
# short-tale publisher.
#
# A real Chromium in a virtual display, with noVNC so you can reach in and
# sign in to YouTube by hand exactly once. After that the session cookie
# lives in the /profile volume and uploads are automatic.
#
# There are deliberately NO account credentials anywhere in this image, this
# repo, or the environment. Nothing to leak.
# ---------------------------------------------------------------------------
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1280x1024x24

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11vnc fluxbox novnc websockify \
      tini curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/publisher/requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --upgrade pip && python3 -m pip install -r /tmp/requirements.txt

COPY src/shorttale/publish /app/publisher
COPY docker/publisher/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

VOLUME ["/profile"]
EXPOSE 7900 8090

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start.sh"]
