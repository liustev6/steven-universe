#!/usr/bin/env bash
# Spike 16 in a maintenance window on CT 108 (the live service stopped: the GPU holds one copy of the
# models). Run on the Mac, from speech-service's folder:   bash spikes/16-streaming-text/run.sh [runs]
# The WAVs come back to $WAVS on the Mac. The live service is started again at the end, whatever happens.
set -uo pipefail
HOST=speech-host
LIVE=speech-service-speech-service-1
IMAGE=$(ssh $HOST "docker inspect -f '{{.Config.Image}}' $LIVE")
OUT=spikes/16-streaming-text
WAVS=${WAVS:-/tmp/spike16}
log() { printf '\n==> %s  %s\n' "$(date +%H:%M:%S)" "$*"; }

restore() {
  log "the live service back"
  ssh $HOST "docker rm -f spike16 >/dev/null 2>&1; docker start $LIVE >/dev/null"
  for _ in $(seq 1 60); do
    curl -fs -o /dev/null https://speech.lan.liustev6.ca/health && { log "live and warm"; return; }
    sleep 5
  done
  log "WARNING: the live service isn't healthy yet: ssh $HOST docker logs $LIVE"
}
trap restore EXIT

log "the dev code to $HOST:/opt/speech-dev"
COPYFILE_DISABLE=1 tar -czf - --exclude __pycache__ app spikes | ssh $HOST 'cd /opt/speech-dev && rm -rf stage && mkdir stage \
  && tar -xzf - -C stage 2>/dev/null && rm -rf app spikes && mv stage/app stage/spikes . && rm -rf stage \
  && rm -rf out/16 && mkdir -p out/16 && chmod 777 out/16'

log "stopping the live service"
ssh $HOST "docker stop $LIVE >/dev/null"

log "spike 16"
ssh $HOST "docker run --rm --name spike16 --gpus all -v /opt/speech-dev:/src -w /src -e PYTHONPATH=/src \
  -v /var/lib/speech-service/models:/models:ro -v /var/lib/speech-service/models:/spike-models:ro \
  -v /opt/speech-dev/out/16:/out $IMAGE python spikes/16-streaming-text/compare.py ${1:-2}" 2>&1 \
  | grep -v -i "flash-attn\|sox\|pad_token_id\|warn\|^\s*$\|\*\*\*\|path variables\|If you do\|- - -" | tee $OUT/results.txt

log "the WAVs to $WAVS"
mkdir -p "$WAVS" && scp -q "$HOST:/opt/speech-dev/out/16/*.wav" "$WAVS/" && ls "$WAVS" | wc -l
