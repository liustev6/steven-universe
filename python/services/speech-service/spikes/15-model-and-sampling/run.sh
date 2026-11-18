#!/usr/bin/env bash
# Spike 15: the 1.7B model and the talker's sampling, in a maintenance window on CT 108 (the live
# service stopped: the GPU can't hold a second engine). Run on the Mac, from speech-service's folder,
# with the test client running (.venv/bin/python test-client/server.py):
#
#   bash spikes/15-model-and-sampling/run.sh 1     # window 1: sampling on the 0.6B; the 1.7B
#   bash spikes/15-model-and-sampling/run.sh 2     # window 2: Qwen's reference; both models with Auto; 2 + 2
#
# 1. Qwen's own generation on spike 14's sentences (reference.py): is ours the same as the model's?
# 2. Each variant as the real service (the image, the dev code from /opt/speech-dev/app, its settings
#    as SPEECH_* variables) on port 443, measured by spike 14's rate.py through the test client; the
#    1.7B also by e2e.py (2 + 2 at once: does it fit, and keep up?) and its VRAM.
# The live service is started again at the end, whatever happens.
set -uo pipefail
HOST=speech-host
LIVE=speech-service-speech-service-1
IMAGE=$(ssh $HOST "docker inspect -f '{{.Config.Image}}' $LIVE")
OUT=spikes/15-model-and-sampling
SPIKE_MODELS=/var/lib/tts-spike/models
log() { printf '\n==> %s  %s\n' "$(date +%H:%M:%S)" "$*"; }

restore() {
  log "the live service back"
  ssh $HOST "docker rm -f spike15 >/dev/null 2>&1; docker start $LIVE >/dev/null"
  for _ in $(seq 1 60); do
    curl -fs -o /dev/null https://speech.lan.liustev6.ca/health && { log "live and warm"; return; }
    sleep 5
  done
  log "WARNING: the live service isn't healthy yet: ssh $HOST docker logs $LIVE"
}
trap restore EXIT

WINDOW=${1:-2}
log "the dev code to $HOST:/opt/speech-dev (app/ and spikes/)"
COPYFILE_DISABLE=1 tar -czf - --exclude __pycache__ app spikes | ssh $HOST 'cd /opt/speech-dev && rm -rf stage && mkdir stage \
  && tar -xzf - -C stage 2>/dev/null && rm -rf app spikes && mv stage/app stage/spikes . && rm -rf stage'

log "stopping the live service ($IMAGE)"
ssh $HOST "docker stop $LIVE >/dev/null"

if [ "$WINDOW" = 2 ]; then
log "1. Qwen's reference (0.6B)"
ssh $HOST "docker run --rm --gpus all -v /opt/speech-dev:/src -w /src -v /var/lib/speech-service/models:/models:ro \
  -e PYTHONPATH=/src $IMAGE python spikes/15-model-and-sampling/reference.py /models/Qwen3-TTS-12Hz-0.6B-CustomVoice 6" \
  2>&1 | grep -v -i "flash-attn\|sox\|warn" | tee $OUT/reference-0.6B.txt
fi

variant() {                       # variant NAME MODEL [SPEECH_X=y ...]
  local name=$1 model=$2; shift 2
  local envs="-e SPEECH_TTS_MODEL=$model"
  for kv in "$@"; do envs="$envs -e $(printf %q "$kv")"; done
  log "2. variant $name: $model $*"
  ssh $HOST "docker rm -f spike15 >/dev/null 2>&1; docker run -d --name spike15 --gpus all --read-only --user 10001:10001 \
    --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:exec,size=2g -p 443:8443 \
    --env-file /etc/speech-service/config.env $envs \
    -v /etc/speech-service/tls:/etc/speech-service/tls:ro -v /var/lib/speech-service/models:/models:ro \
    -v $SPIKE_MODELS:/spike-models:ro -v /opt/speech-dev/app:/app/app:ro $IMAGE >/dev/null"
  local t0=$SECONDS ok=
  for _ in $(seq 1 90); do
    curl -fs -o /dev/null https://speech.lan.liustev6.ca/health && { ok=1; break; }
    ssh $HOST "docker ps -q -f name=spike15" | grep -q . || break
    sleep 4
  done
  {
    echo "variant $name: $model $*"
    ssh $HOST "docker logs spike15 2>&1 | grep -E 'models loaded|warm in|Error|error' | head -5"
    if [ -z "$ok" ]; then echo "NOT HEALTHY after $((SECONDS - t0)) s"; ssh $HOST "docker logs spike15 2>&1 | tail -30"; return; fi
    echo "healthy after $((SECONDS - t0)) s; VRAM: $(ssh $HOST nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
    .venv/bin/python spikes/14-speaking-rate/rate.py --runs 4 --voices ryan,serena 2>&1
  } > $OUT/variant-$name.txt 2>&1
  sed -n '/^== per sentence/,$p' $OUT/variant-$name.txt
}

e2e() {                           # 2 + 2 at once on the variant that's up: does it fit, and keep up?
  local name=$1 TOKEN
  TOKEN=$(.venv/bin/python -c "
import asyncio, pathlib
from service_auth import TokenClient
t = TokenClient('https://auth.lan.liustev6.ca', 'speech-test-client', (pathlib.Path.home() / '.config/speech-test-client/secret').read_text().strip())
print(asyncio.run(t.token('speech-service')))")
  log "$name: 2 + 2 at once (e2e.py)"
  { .venv/bin/python scripts/e2e.py https://speech.lan.liustev6.ca --token "$TOKEN" 2>&1 | tail -25
    echo "VRAM after 2 + 2: $(ssh $HOST nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
  } 2>&1 | tee $OUT/variant-$name-e2e.txt
}

INSTRUCT="SPEECH_TTS_INSTRUCT=Speak at a steady, natural pace, without long pauses. 用平稳自然的语速说话，不要拖长或停顿。"
M06=/models/Qwen3-TTS-12Hz-0.6B-CustomVoice
M17=/spike-models/Qwen3-TTS-12Hz-1.7B-CustomVoice
if [ "$WINDOW" = 1 ]; then          # (window 1 ran with the voice's own language as the default)
  variant 0.6B-t0.7-k20 $M06 SPEECH_TTS_TEMPERATURE=0.7 SPEECH_TTS_TOP_K=20
  variant 0.6B-t0.6-k10 $M06 SPEECH_TTS_TEMPERATURE=0.6 SPEECH_TTS_TOP_K=10
  variant 1.7B $M17
  variant 1.7B-instruct $M17 "$INSTRUCT"
  variant 1.7B-t0.7-k20 $M17 SPEECH_TTS_TEMPERATURE=0.7 SPEECH_TTS_TOP_K=20
else                               # window 2: the language defaults to Auto
  variant 0.6B-auto $M06
  variant 1.7B-instruct-auto $M17 "$INSTRUCT"
  e2e 1.7B-instruct-auto
fi
log "done"
