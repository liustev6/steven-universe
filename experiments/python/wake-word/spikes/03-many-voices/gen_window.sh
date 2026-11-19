#!/usr/bin/env bash
# Spike 03's generation on CT 108's GPU, in a maintenance window: the code, the bank and the reference clips
# to /var/lib/experiments, the live speech-service stopped, gen_voices.py run for each source, then the live
# service started again, whatever happens (as scripts/gpu_window.sh). Run on the Mac from wake-word/:
#
#   bash spikes/03-many-voices/gen_window.sh "design clone"           (LIMIT=2: a speed test, 2 voices each)
set -uo pipefail
HOST=speech-host
LIVE=speech-service-speech-service-1
SOURCES=${1:-design clone}
LIMIT=${LIMIT:-0}
log() { printf '\n==> %s  %s\n' "$(date +%H:%M:%S)" "$*"; }

restore() {
  log "the live service back"
  ssh $HOST "docker rm -f exp-gen >/dev/null 2>&1; docker start $LIVE >/dev/null"
  for _ in $(seq 1 60); do
    curl -fs -o /dev/null https://speech.lan.liustev6.ca/health && { log "live and warm"; return; }
    sleep 5
  done
  log "WARNING: the live service isn't healthy yet: ssh $HOST docker logs $LIVE"
}
trap restore EXIT

log "code, bank and reference clips to $HOST:/var/lib/experiments"
ssh $HOST "mkdir -p /var/lib/experiments/code /var/lib/experiments/refs"
scp -q spikes/03-many-voices/gen_voices.py phrases/xiao_pai_bank.yaml $HOST:/var/lib/experiments/code/
rsync -a data/datasets/aishell3-refs/ $HOST:/var/lib/experiments/refs/
IMAGE=$(ssh $HOST "docker inspect -f '{{.Config.Image}}' $LIVE")

log "stopping the live service"
ssh $HOST "docker stop $LIVE >/dev/null"

for source in $SOURCES; do
  log "generating: $source (limit ${LIMIT})"
  ssh $HOST "docker run --rm --name exp-gen --gpus all -v /var/lib/experiments:/exp --entrypoint python $IMAGE \
    /exp/code/gen_voices.py --source $source --limit $LIMIT" 2>&1 | grep -vE "Warning|warn\(|SoX|path variables|^\s*$"
done
