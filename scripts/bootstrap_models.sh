#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time model download. Everything lands in ./models or the ollama volume
# and is reused forever after — this is the only step that needs the internet
# for anything other than content.
#
#   ./scripts/bootstrap_models.sh          # LLM + Kokoro + Whisper
#   PIPER=1 ./scripts/bootstrap_models.sh  # also fetch a Piper fallback voice
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && set -a && . ./.env && set +a

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:14b-instruct-q4_K_M}"
OLLAMA_MODEL_FAST="${OLLAMA_MODEL_FAST:-qwen2.5:7b-instruct-q4_K_M}"
MODELS_DIR="./models"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

mkdir -p "$MODELS_DIR/kokoro" "$MODELS_DIR/whisper" "$MODELS_DIR/piper"

# --- 1. LLM ---------------------------------------------------------------
say "Pulling language models into the ollama volume"
if ! docker compose ps ollama --status running >/dev/null 2>&1; then
  echo "    starting ollama..."
  docker compose up -d ollama
  sleep 8
fi
for m in "$OLLAMA_MODEL" "$OLLAMA_MODEL_FAST"; do
  echo "    $m"
  docker compose exec -T ollama ollama pull "$m" || {
    echo "    !! could not pull $m — check the name at https://ollama.com/library"
    echo "       (set OLLAMA_MODEL in .env to any model you prefer)"
  }
done

# --- 2. Kokoro speech -----------------------------------------------------
say "Downloading Kokoro speech model (~350MB)"
KOKORO_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
fetch() {
  local url="$1" dest="$2"
  if [ -s "$dest" ]; then echo "    have $(basename "$dest")"; return 0; fi
  echo "    $(basename "$dest")"
  curl -fL --retry 3 --progress-bar -o "$dest.part" "$url" && mv "$dest.part" "$dest"
}
fetch "$KOKORO_BASE/kokoro-v1.0.onnx"  "$MODELS_DIR/kokoro/kokoro-v1.0.onnx"
fetch "$KOKORO_BASE/voices-v1.0.bin"   "$MODELS_DIR/kokoro/voices-v1.0.bin"

# --- 3. Whisper (caption timing) ------------------------------------------
say "Warming the Whisper caption model"
docker compose run --rm worker python3 -c "
from shorttale.tts.captions import get_model
get_model(); print('whisper ready')
" || echo "    !! whisper warm-up failed — it will download on first use instead"

# --- 4. Piper (optional fallback voice) -----------------------------------
if [ "${PIPER:-0}" = "1" ]; then
  say "Downloading a Piper fallback voice"
  PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium"
  fetch "$PIPER_BASE/en_US-amy-medium.onnx"      "$MODELS_DIR/piper/en_US-amy-medium.onnx"
  fetch "$PIPER_BASE/en_US-amy-medium.onnx.json" "$MODELS_DIR/piper/en_US-amy-medium.onnx.json"
fi

say "Done"
echo "    models/  $(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1) on disk"
echo "    next:    make doctor"
