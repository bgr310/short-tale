# ---------------------------------------------------------------------------
# short-tale application image (API, scheduler, and pipeline worker).
#
# CUDA runtime + cuDNN because faster-whisper (CTranslate2) needs cuDNN for
# GPU transcription. If no GPU is visible at runtime, every GPU stage detects
# that and falls back to CPU — the container still works, just slower.
# ---------------------------------------------------------------------------
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/models/hf \
    XDG_CACHE_HOME=/app/models/cache

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.10 python3-pip python3-venv \
      ffmpeg \
      espeak-ng \
      fonts-dejavu-core fonts-liberation2 fonts-noto-color-emoji \
      curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# NVENC is provided by the host driver and injected by the container toolkit.
# Nothing to install — but ffmpeg must be able to find it at runtime.
ENV LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/lib/wsl/lib:${LD_LIBRARY_PATH}

WORKDIR /app

COPY docker/app/requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --upgrade pip setuptools wheel \
    && python3 -m pip install -r /tmp/requirements.txt

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY tests /app/tests
RUN python3 -m pip install --no-deps -e /app

# Non-root. Volumes are chowned by the entrypoint because bind-mount
# ownership is decided by the host, not the image.
RUN useradd -m -u 1000 shorttale && mkdir -p /app/data /app/out /app/models \
    && chown -R shorttale:shorttale /app

COPY docker/app/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["shorttale", "serve"]
