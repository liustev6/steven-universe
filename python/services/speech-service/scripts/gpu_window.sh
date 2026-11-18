#!/usr/bin/env bash
# A maintenance window on CT 108 for GPU work that can't run beside the live service (the GPU holds one
# copy of the models): the dev code's GPU tests, then the dev code as the service on 443 (e2e.py, and a
# reply streamed in pieces, kept as a WAV). The live service is started again at the end, whatever
# happens. Run on the Mac from speech-service's folder, with the test client running:
#
#   bash scripts/gpu_window.sh "tests/test_stream_gpu.py tests/test_batch_gpu.py"   (TESTS_ONLY=1: just the tests)
set -uo pipefail
HOST=speech-host
LIVE=speech-service-speech-service-1
IMAGE=$(ssh $HOST "docker inspect -f '{{.Config.Image}}' $LIVE")
TESTS=${1:-tests}
OUT=${OUT:-/tmp/gpu-window}
TTS=/models/$(grep -E '^SPEECH_TTS_MODEL=' ../../../infra/services/speech-service/config.env | sed 's|.*/||')
log() { printf '\n==> %s  %s\n' "$(date +%H:%M:%S)" "$*"; }

restore() {
  log "the live service back"
  ssh $HOST "docker rm -f gpu-window >/dev/null 2>&1; docker start $LIVE >/dev/null"
  for _ in $(seq 1 60); do
    curl -fs -o /dev/null https://speech.lan.liustev6.ca/health && { log "live and warm"; return; }
    sleep 5
  done
  log "WARNING: the live service isn't healthy yet: ssh $HOST docker logs $LIVE"
}
trap restore EXIT
mkdir -p "$OUT"

log "the dev code to $HOST:/opt/speech-dev"
COPYFILE_DISABLE=1 tar -czf - --exclude __pycache__ app tests scripts spikes pytest.ini requirements-dev.txt 2>/dev/null \
  | ssh $HOST 'cd /opt/speech-dev && rm -rf stage && mkdir stage && tar -xzf - -C stage 2>/dev/null \
      && for d in app tests scripts spikes; do rm -rf $d; mv stage/$d .; done; mv stage/* . 2>/dev/null; rm -rf stage'

log "stopping the live service"
ssh $HOST "docker stop $LIVE >/dev/null"

log "1. GPU tests with $TTS: each file in its own process (one copy of the 1.7B fits the GPU)"
: > "$OUT/gpu-tests.txt"
for f in $TESTS; do
  ssh $HOST "docker run --rm --name gpu-window --user 0 --gpus all -v /opt/speech-dev:/src -w /src -e PYTHONPATH=/src:/libs/service-auth \
    -v /var/lib/speech-service/models:/models:ro -e TTS_MODEL=$TTS -e STT_MODEL=/models/Qwen3-ASR-0.6B \
    $IMAGE sh -c 'pip install -q pytest==9.2.0 2>/dev/null; python -m pytest -q -m gpu -p no:cacheprovider -rfE --tb=short $f 2>&1'" \
    2>&1 | grep -v -i "flash-attn\|sox\|warn\|pad_token_id" | tee -a "$OUT/gpu-tests.txt" | tail -4
done
[ "${TESTS_ONLY:-}" = 1 ] && exit 0

log "2. the dev code as the service"
ssh $HOST "docker run -d --name gpu-window --gpus all --read-only --user 10001:10001 --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp:exec,size=2g -p 443:8443 --env-file /etc/speech-service/config.env \
  -v /etc/speech-service/tls:/etc/speech-service/tls:ro -v /var/lib/speech-service/models:/models:ro \
  -v /opt/speech-dev/app:/app/app:ro $IMAGE >/dev/null"
for _ in $(seq 1 90); do curl -fs -o /dev/null https://speech.lan.liustev6.ca/health && break; sleep 4; done
TOKEN=$(.venv/bin/python -c "
import asyncio, pathlib
from service_auth import TokenClient
t = TokenClient('https://auth.lan.liustev6.ca', 'speech-test-client', (pathlib.Path.home() / '.config/speech-test-client/secret').read_text().strip())
print(asyncio.run(t.token('speech-service')))")
.venv/bin/python scripts/e2e.py https://speech.lan.liustev6.ca --token "$TOKEN" 2>&1 | tail -12 | tee "$OUT/e2e.txt"
.venv/bin/python - "$OUT" <<'PY' 2>&1 | tee "$OUT/streamed.txt"
import asyncio, json, sys, time, wave
import websockets
OUT = sys.argv[1]
REPLIES = {
    "en": ("ryan", ["Old Thomas spent forty years ", "in a cramped, sunlit shop at the edge of town. ",
                    "To Thomas, time was not a master to be feared, ", "nor a thief to be cursed. ",
                    "It was a guest he tended with oil, tweezers, and patience."]),
    "zh": ("dylan", ["好的，", "我帮你查了一下：明天Seattle会下小雨，", "最高气温12度，最低7度。",
                     "记得带umbrella，出门前也可以先看看Google Maps上的traffic。"]),
}
async def speak(name, voice, pieces):
    pcm, msgs, t0, first = bytearray(), [], time.perf_counter(), None
    async with websockets.connect(f"ws://localhost:8765/v1/realtime/speech?voice={voice}", max_size=None) as ws:
        async def write():
            for p in pieces:                                   # as an agent writes: a piece every 150 ms
                await ws.send(json.dumps({"type": "text", "text": p}))
                await asyncio.sleep(0.15)
            await ws.send(json.dumps({"type": "end"}))
        writer = None
        async for m in ws:
            if isinstance(m, bytes):
                first = first or time.perf_counter() - t0
                pcm += m
                continue
            msgs.append(json.loads(m))
            if msgs[-1]["type"] == "started":
                t0, writer = time.perf_counter(), asyncio.create_task(write())
            if msgs[-1]["type"] in ("done", "error"):
                break
    with wave.open(f"{OUT}/streamed-{name}.wav", "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(bytes(pcm))
    print(f"{name}: first audio {first * 1000:.0f} ms, {len(pcm) / 48000:.1f} s of speech, "
          f"{sum(m['type'] == 'sentence' for m in msgs)} pieces, ends {msgs[-1]}")
async def main():
    for name, (voice, pieces) in REPLIES.items():
        await speak(name, voice, pieces)
asyncio.run(main())
PY
log "done: $OUT"
