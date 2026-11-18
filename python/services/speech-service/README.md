# speech-service
Speech out and in, streamed, from two models kept loaded on pve01's GPU (CT 108): Qwen3-TTS-0.6B
(text to speech) and Qwen3-ASR-0.6B (speech to text), in one process. Spec: [speech-service.md](../../../docs/specs/speech-service.md).

| Path | What |
|---|---|
| `app/tts_engine/` | text to speech: Qwen's model with our loops, fused Triton kernels and CUDA graphs; the continuous batch (`batch.py`) |
| `app/stt_engine/` | speech to text: Qwen3-ASR, streamed (Qwen's re-feed method on our loop); the batch; server turn detection |
| `app/work/` | admission (2 running + 1 waiting per kind), sentences, a work thread per model |
| `app/api/` | the WebSocket sessions, the one-shot POST, voices, status, health |
| `app/models.py` | the weights: fetched at their pinned revision, every file's SHA-256 checked |
| `app/main.py` | listens at once, loads and warms both models through the work threads, then healthy |
| `base-image/` | speech-base: the model stack (PyTorch + CUDA 13.2 libraries, Triton, qwen-tts, qwen-asr), ~7 GB, built by CI from a lock |
| `Dockerfile` | the service image: speech-base (pinned by digest) + our code, COPY only |
| `scripts/` | `smoke_test.py` (a sentence spoken, then heard back), `cert-hook.sh`, `bench.py`, `chunks.py`, `e2e.py` |
| `test-client/` | a local page that talks to the live service and plays it, as `speech-test-client` (its server forwards the WebSockets with the token) |
| `spikes/` | every measurement behind the design, with its code and raw output |

## Configuration
`infra/services/speech-service/config.env` → `/etc/speech-service/config.env` (settings: `app/config.py`,
prefix `SPEECH_`). No identity of its own (it calls no service); tokens are checked against
auth-service's public keys. The weights: `infra/services/speech-service/models.yaml`, fetched by the
pre-start hook into `/var/lib/speech-service/models`, mounted read-only.

## Deploy (by hand for now)
```sh
infra/deployment/deploy.sh speech-service
```
First time: the deploy stops at the certificate check; then on CT 108, once:
```sh
install -m 755 /opt/steven-universe/python/services/speech-service/scripts/cert-hook.sh /usr/local/sbin/speech-service-cert-hook
certbot certonly --dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
  -d speech.lan.liustev6.ca --deploy-hook /usr/local/sbin/speech-service-cert-hook
```
and deploy again. **Nothing else may hold the GPU** (a dev service, a spike): one copy of both
models fills most of its 8 GB, and a start beside another fails out of memory. A new base image: change `base-image/requirements.lock` or its Dockerfile, push
(CI publishes `speech-base`), then pin its digest in `Dockerfile`'s `BASE` and push again.

## Tests
```sh
cd python/services/speech-service && uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q                # CPU: the logic, the API with fake models
```
The GPU tests (`-m gpu`: the engines, kernels, batches, the round trip) run on CT 108 in the image,
with `TTS_MODEL` and `STT_MODEL` set (see `tests/test_tts_gpu.py`).
