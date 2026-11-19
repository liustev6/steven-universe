#!/usr/bin/env bash
# On the playground (CT 110): an experiment tool run with speech-service's code importable, on production's
# exact stack (speech-base, pinned by digest in the service's Dockerfile) and models (fetched and checked
# against infra/services/speech-service/models.yaml, as a deploy does). The repository at /srv/src.
#
#   run.sh models                      production's pinned models into /srv/experiments/speech-service/models (once)
#   run.sh check                       the engine imports (no GPU)
#   run.sh trace_reply.py /models/Qwen3-TTS-12Hz-1.7B-CustomVoice dylan /out/trace.wav      (GPU)
#   run.sh shell                       a shell in the same container
#
# Inside: /src speech-service's code (PYTHONPATH), /exp this folder, /models, /out (both on the playground's
# disk). GPU work needs the card: stop speech-service on CT 108 first, by hand (docs/playground.md); this
# refuses to start while it still holds the memory.
set -euo pipefail
REPO=${REPO:-/srv/src/steven-universe}
DATA=${DATA:-/srv/experiments/speech-service}
BASE=$(grep -oE "ghcr.io/liustev6/steven-universe/speech-base@sha256:[0-9a-f]+" "$REPO/python/services/speech-service/Dockerfile")
mkdir -p "$DATA/models" "$DATA/out"
run() {
  docker run --rm -i $([ -t 0 ] && echo -t) --gpus all \
    -v "$REPO/python/services/speech-service:/src:ro" -v "$REPO/python/libs/service-auth:/libs/service-auth:ro" \
    -v "$REPO/infra/services/speech-service:/infra:ro" -v "$REPO/experiments/python/speech-service:/exp:ro" \
    -v "$DATA/models:/models" -v "$DATA/out:/out" -w /src -e PYTHONPATH=/src:/libs/service-auth \
    --entrypoint "$1" "$BASE" "${@:2}"
}
case "${1:-}" in
  models) run python -m app.models fetch /infra/models.yaml /models ;;
  check)  run python -c "
from app.tts_engine.engine import QwenTTSEngine
from app.work.normalize import TextNormalizer
from app.work.reply_text import QwenReplyText, joined
from app.work.sentences import Sentences
import torch, qwen_tts
print('speech-service imports: ok; torch', torch.__version__, '| qwen-tts', getattr(qwen_tts, '__version__', '?'))" ;;
  shell)  run bash ;;
  *.py)
    free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
    if [ "$free" -lt 6000 ]; then
      echo "only ${free} MiB of GPU memory free: stop speech-service first (ssh speech-host docker stop speech-service-speech-service-1)" >&2
      exit 1
    fi
    run python "/exp/$1" "${@:2}" ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
