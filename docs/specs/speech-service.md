# Spec: speech-service (speech out and in, streamed, from models that stay loaded on the GPU)

**Renamed from tts-service (2026-11-17):** speech-to-text joins text-to-speech in the same process on
the same GPU (T12), so the service is named for both. Text-to-speech is built (4.5); speech-to-text is
next, its model chosen after pi-server's speech-to-text spike.

**Status: agreed** (2026-11-17). The first service on pve01's GPU, and the start of
[agent-service](agent-service/README.md)'s CP4 (voice): its build plan below **is** CP4's first
sub-checkpoints ([tracker.md](agent-service/tracker.md)). Related: [lxc-setup.md](../lxc-setup.md),
[boot.md](boot.md), [auth-service.md](auth-service.md), [deployer.md](deployer.md).

## What it is
A small service that turns text into speech and **streams the audio as it's made**, from **one
long-running process that keeps the model loaded** on the GPU. There's no cold load per request: the
model is loaded and warmed once at start, before the service reports healthy, and stays in VRAM until
the process stops (a deploy, a reboot).

```
caller (pi-server, a smoke test, later the voice path)
  └─(token, aud=speech-service)─▶ CT 108 :443  POST /v1/audio/speech {input, voice}
                                  │  one queue ─▶ one GPU worker ─▶ Qwen3-TTS 0.6B (held in VRAM)
                                  ◀═ audio chunks, streamed as each one is generated (PCM, 24 kHz)
pve01: NVIDIA driver (open kernel modules) ── /dev/nvidia* ──▶ CT 108 (unprivileged LXC) ──▶ Docker
```

**Goal (the first checkpoint's "done when"):** the service is up; a request's audio streams back in
packets, the first one quickly; consecutive requests never pay a model load; a restart is warm again
before it's healthy.

## Decisions
| ID | Decision | Why |
|---|---|---|
| T1 | **The GPU goes to an LXC container, CT 108**, not a VM: the NVIDIA driver on pve01, the device nodes given to the container with Proxmox's own `devN` entries (no raw `lxc.*` lines), the same driver's user-space libraries inside, Docker with the NVIDIA Container Toolkit (`no-cgroups = true`, as unprivileged containers need). | Lighter (no second kernel, no reserved VM memory), and the card can serve more than one container (speech-to-text next). The cost is weaker isolation than a VM: acceptable for our own trusted code, which is why **agent sandboxes never get the GPU**. |
| T2 | **The GPU serves only voice for now.** The old gpu-server is archived; a general GPU service is redesigned when there are several GPUs. | One GPU, one purpose: no scheduling between unrelated workloads. |
| T3 | **One process holds the model, loaded and warmed at start.** It loads the weights, runs a short warm-up synthesis (and captures CUDA graphs if the runtime uses them), and only then answers `/health`. Docker's health check gives it a long start period. It never unloads. | No cold load on any request: the goal. A restart costs one load, at deploy or boot time, not at a caller's. |
| T4 | **The model:** `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` (preset voices; Chinese and English), **pinned to revision `85e237c12c027371202489a0ec509ded67b5e4b5`**. Its weights live on CT 108's disk (`/var/lib/speech-service/models`), fetched once by the deploy (the pre-start hook, running the new image), checked against their hashes; at run time the service is offline (`HF_HUB_OFFLINE=1`). | About 2 GB of VRAM at FP16, of the card's 8 GB: room for speech-to-text later. Pinned weights are a reviewed input like any image; not baking them into the image keeps CI's images small. |
| T11 | **Our own engine, on Qwen's official code (`qwen-tts`, Apache-2.0), with our own CUDA graphs.** The official package doesn't stream (it returns whole sentences; its docs say streaming is only simulated). Ours: the talker generates in a background thread; each frame's 16 codes are collected as they're made; the codec decodes them in chunks of 1, 1, 2, then every 4 frames (a frame: 80 ms of audio), each with up to 25 frames of earlier context, and only the new audio is sent. Then our own **CUDA graphs**: the code predictor's 15 steps per frame, then the talker's step on a static KV cache, captured at warm-up and replayed per frame. The plain-PyTorch path stays as the reference the tests compare against. | Streaming is the goal; no third-party wrapper (the one that exists, `faster-qwen3-tts`, broke on a current `transformers` in the spike). Graphs: ~1.5× faster generation and ~55 ms earlier first audio, measured; and building them is the CP's learning goal. |
| T5 | **The API is OpenAI's speech API shape:** `POST /v1/audio/speech` `{input, voice, response_format, …}`, the audio streamed back in chunks (`Transfer-Encoding: chunked`). Default format **`pcm`**: raw 16-bit little-endian mono at **24 kHz** (the codec's rate), the lowest-latency format; also `wav` (a streaming header, length unknown). | A known contract: clients and tools that speak OpenAI's TTS API work as they are. Raw PCM needs no encoder in the loop, so the first packet leaves as soon as it's generated. |
| T6 | ~~**One GPU worker, one queue.** Requests are generated one at a time, in order; a short queue (4) beyond that is refused with `503` and `Retry-After`.~~ Revised by T13 (one thread still owns the GPU; it runs batches of 2). **A caller that disconnects cancels its generation** (that's how the voice path stops speech when you interrupt). | One model on one GPU: parallel generation would only slow each other's first packet. Cancelling on disconnect frees the GPU at once (barge-in). |
| T7 | **Auth like the other services:** auth-service tokens, audience `speech-service`, scope `speech:create`; `status:read` for api-service's status page. Callers in CP4: `smoke-test` (deploy checks, which synthesize for real: it costs nothing but GPU time), then pi-server. | The same identity model as everything else (auth-service.md); no shared keys. |
| T8 | **Limits:** a reply's text at most 4,096 characters (OpenAI's). ~~Callers send **a sentence at a time**, as separate requests~~; revised by T13: **a reply's text streams into one session** as the caller writes it (a sentence or a few words at a time), so speech starts while the rest is still being written and no other caller can take the slot between sentences | Short inputs keep the first packet early; one session per reply keeps it whole: separate requests per sentence could lose the slot mid-reply, and each would pay a new prefill |
| T10 | **speech-service is an engine; pi-server orchestrates the voice pipeline; agent-service stays text-only.** pi-server (Hey Pi's backend) turns speech into text, sends the turn to agent-service, collects its streamed reply into sentences, and sends each sentence to speech-service as soon as it's complete, playing the audio in order while the agent is still writing (a chained, overlapping pipeline, the cascaded voice-agent design of Pipecat or LiveKit Agents). On barge-in it cancels the agent's turn and closes the audio stream. pi-server calls both directly on the LAN, with its own grants (`chat:*` at agent-service, `speech:create` here). | Each piece does one job: speech-service knows nothing of chat (any client can use it: the phone, a notification); agent-service carries no audio its text clients don't need; the conversation's timing (sentences, playback, barge-in) lives in one place. You hear the first word after the first sentence and its first audio packet, not after the whole reply. |
| T9 | **Deployed like every service:** its manifest, by hand first, then the pipeline; on the lab's status page (S rule): model and revision, warm or loading, VRAM used, queue length, recent first-packet times. | Nothing new to learn or operate. |
| T12 | **Speech-to-text runs in this service, in the same process as text-to-speech** (decided 2026-11-17, revising the first review's "its own container"); the Pi keeps the wake word and voice-activity detection, and streams audio here only while someone speaks. The model: chosen after pi-server's speech-to-text spike, and it must load in the same Python environment (PyTorch 2.15 with CUDA 13.2; transformers 4.57.3, which `qwen-tts` pins) | **One CUDA context** instead of two (a few hundred MB of VRAM each). **Real sharing of the GPU:** two processes on one GPU take turns in time slices and their kernels never overlap; in one process, CUDA streams with priorities let speech-to-text listen while text-to-speech speaks (barge-in) without stalling it (4.5c's streams). One warm-up, deploy, health check, one place that schedules the GPU: how NVIDIA Riva and Triton Inference Server host several models on a shared GPU. Off the Pi: it has no fan and throttled at 83 °C in the spike's first test, and a GPU runs far larger models (the Pi's made ~20% errors in English). The cost: a restart restarts both (~10 s warm-up) |
| T13 | **Concurrency: batches of 2 per model, sessions apart from work** (2026-11-17). Up to **2 TTS + 2 speech-to-text streams at once**, each model running its streams as **one batch** (continuous batching: streams join between steps and leave when they end). **A session is one WebSocket connection, for both kinds**: TTS (a reply's text streamed in, its audio streamed out) and speech-to-text (an utterance's audio in, partial and final text out). Per kind, **2 running + 1 waiting = 3 sessions accepted**; the waiting one is told `queued`, then `started`; a 4th is refused at the handshake (`503`, `Retry-After`). **A session releases its slot by itself:** when its stream ends (TTS: the client's `end`, then the last audio, `done`; speech-to-text: the client's `commit`, then the `final` text), on `cancel` or disconnect (at once: barge-in), when idle (TTS: nothing left to speak and no new text for 15 s; speech-to-text: no audio for 10 s), when too long (a reply 10 min, an utterance 60 s), or after 10 s waiting; one reply or one utterance per session, then it closes (the limits: config). Sessions never touch the GPU; one work thread owns it and exchanges queues with them. **No database:** a session lives seconds to a minute, its state is in memory and in `/v1/status`; the history (what was said) belongs to the caller (agent-service's PostgreSQL). Order: speech-to-text (4.5d), then batching (4.5e), then the service (4.5f) | Steven AI and pi-server may both use it at once. Batching (not taking turns): the step's cost is reading the weights (spike 06: 83% matrix multiplies), read once for both streams, so 2 streams cost ~1.3× one, not 2×; a joiner pays one prefill (~8 ms), absorbed by the running stream's 66 ms margin (vLLM's and Orca's continuous batching). Refusing fast beats a long queue: a voice reply that starts seconds late is worse than a retry. A session per reply (not a request per sentence): the slot can't be lost mid-reply (ElevenLabs' and Cartesia's input streaming, OpenAI Realtime); a session waiting for text costs no GPU (it sits out of the batch), the idle timeout stops it holding a slot forever. Serving engines (vLLM, Triton, Riva) keep no history; their callers do |

## API (443)
| Route | Scope | What |
|---|---|---|
| `WS /v1/realtime/speech` (4.5f) `?voice=…&language=…` | `speech:create` | a TTS session for one reply: client `{type: text, text}` (as it's written), `{type: end}`, `{type: cancel}`; server `queued`, `started`, binary audio frames (24 kHz PCM), `done`, `error` (with `idle`, `too_long`, `queue_timeout`); refused at the handshake with `503` when 3 sessions are already accepted |
| `POST /v1/audio/speech` `{input, voice, language?, instructions?, response_format?: "pcm" \| "wav", speed?}` | `speech:create` | a one-shot TTS session (smoke tests, simple callers): audio, streamed as it's generated. `X-Sample-Rate`, `X-Request-Id` headers. `400` unknown voice or too long; `503` (`Retry-After`) queue full or still warming |
| `GET /v1/voices` | `speech:create` | the model's preset voices, with their languages |
| `WS /v1/realtime/transcription` (4.5f; OpenAI Realtime-shaped) | a new scope, e.g. `speech:transcribe` (4.5f) | a speech-to-text session for one utterance: client binary audio frames (16 kHz PCM, ~100 ms each, starting with ~300 ms from before the Pi's voice detection fired), `{type: commit}` (end of speech), `{type: cancel}`; server `queued`, `started`, `{type: partial, text}` while audio arrives, `{type: final, text}`, `error`; refused at the handshake with `503` when 3 sessions are already accepted |
| `GET /v1/status` | `status:read` | for the status page: `ok`, `warm`, the models and revisions, GPU name, VRAM used and total, per kind the slots (running, waiting) and sessions, generations so far, last first-packet times (ms) |
| `GET /health` | none | `200` once warm; `503` while loading (Docker's health check waits for it) |

## The container (CT 108)
| | |
|---|---|
| name, DNS, alias | `speech-service`, `speech.lan.liustev6.ca`, `speech-host` |
| size | 4 cores, 8 GB memory (weights pass through memory on load), 64 GB disk (the CUDA image, the weights) |
| boot | `onboot`, order 2 (it needs only auth-service, for token keys) |
| GPU | `dev0` … `devN`: `/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools`, `/dev/nvidia-caps/*` |
| inside | Debian 13, the NVIDIA user-space libraries (the host driver's exact version), Docker, the NVIDIA Container Toolkit; the service's image has PyTorch built for CUDA 12.8 or newer (the RTX 50 series needs it) |

**pve01's kernel and the driver:** the driver is a kernel module, built for each Proxmox kernel by
DKMS. pve01 **follows Proxmox's kernel updates** (decided in 4.2, not pinned): `proxmox-default-headers`
brings each new kernel's headers, DKMS builds the module for it at install, and `./setup.sh reboot`
refuses to reboot into a kernel the module isn't built for. The steps: [lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md).

## The spike (4.5a, 2026-11-17): what the RTX 5060 does
Code, raw output and details: [spikes/01-engine-options](../../python/services/speech-service/spikes/01-engine-options/).
PyTorch 2.15.0 (CUDA 13.2), `qwen-tts` 0.1.1
(transformers 4.57.3), six sentences (four short voice replies, two of 8–9 s; English and Chinese).
"Real-time factor" = generation time ÷ audio length (below 1: faster than speech).

| Run | Real-time factor (median) | First audio (median / worst) | Peak VRAM |
|---|---|---|---|
| A: the official package, whole sentences | 0.47 | 1,735 / 4,177 ms | 2.5 GB |
| **B: our streaming loop, plain PyTorch, 4-frame chunks** | **0.51** | **169 / 173 ms** | 2.5 GB |
| B with 12-frame chunks (0.96 s) | 0.47 | 457 ms | |
| C: a batch of 2 vs one at a time | throughput ×1.75–1.85 (similar lengths) | | |
| **D: CUDA graphs (`faster-qwen3-tts` 0.5.4 as a reference), 4-frame chunks** | **0.33** | **112 / 179 ms** | 2.3 GB |

- Streaming makes the first audio ~10× earlier at the same speed, and nearly independent of the
  sentence's length. The 4-frame chunks sound as good as whole sentences (listened, 2026-11-17).
- Loading: 1.7 s; the first generation ~10 s (CUDA starting up): the warm-up before healthy (T3).
- Graphs: ~27 ms of work per 80 ms frame instead of ~40: the model is small but not purely
  launch-bound, so ×1.5, not more. `faster-qwen3-tts` needed `transformers` pinned to 5.15.1 to run at
  all (5.23 broke its copy of the model code).
- Batching pays off for two callers at once, not for one conversation: one worker generating ahead
  already outruns playback at a real-time factor of 0.3–0.5.

## Build plan (CP4's first sub-checkpoints)
| Sub-CP | What | Done when |
|---|---|---|
| 4.1 ✅ | **the design:** this document | reviewed and agreed (2026-11-17) |
| 4.2 ✅ | **the GPU on pve01:** NVIDIA's open driver 620.70.06 with DKMS, following Proxmox's kernel updates (not pinned) | `nvidia-smi` on pve01 shows the card; the module rebuilds for a new kernel. **Done 2026-11-17:** built for 7.0.2 and 7.0.14, booted 7.0.14 with the RTX 5060 working ([lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md) Part 1) |
| 4.3 ✅ | **CT 108:** the container (lxc-setup part 1), the `devN` entries, the user-space libraries, Docker and the toolkit | `nvidia-smi` inside the container, and inside a CUDA container on its Docker. **Done 2026-11-17:** CT 108 `192.168.50.34`, `speech.lan.liustev6.ca`; `nvidia-smi` in CT 108 and in a CUDA container; NVIDIA's vector-add sample passed as uid 10001 ([lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md) Part 2) |
| 4.4 ✅ | **identity:** auth-service's policy: audience `speech-service` (`speech:create`, `status:read`); `smoke-test` gets `speech:create`, api-service `status:read`. **No client of its own** (it only receives tokens, checked against auth-service's public keys, and calls no service), so no secret | `explain` shows the grants; deployed. **Done 2026-11-17:** live (auth-service's installed policy) |
| 4.5a ✅ | **the spike:** measure the model on the RTX 5060 (above) | done 2026-11-17: our streaming 169 ms, graphs 112 ms |
| 4.5b ✅ | **the engine, plain:** our streaming loop as the `TTSEngine` (load, warm-up, `stream(text, voice, language)` → 24 kHz chunks, voices); the reference for everything after | on CT 108: same audio as the spike's B; a deterministic mode (no sampling) for comparisons. **Done 2026-11-17:** `app/engine/` (`base.py` the interface, `chunker.py` the chunked decode, `qwen.py` the engine, now `tts.py`); 12 CPU tests (the chunker; they found a clipped-tail bug at exact multiples of the chunk) and 9 GPU tests on CT 108: **our codes equal Qwen's own, frame for frame**; streamed audio = the whole decode (>20 dB); cancel within 0.5 s; first audio <300 ms |
| 4.5c ✅ | **CUDA graphs, step by step** (learning; each stage explained, then measured): 1. the code predictor's 15 steps as one graph; 2. the talker's step with a static KV cache; 3. the repetition penalty on the GPU, one end check per frame; 4. the same with `torch.compile` for contrast | each stage measured. **Bit-exact paths** (our loop, our fixed cache, the graph): Qwen's own codes with sampling off. **Fused kernels** (not bit-exact: they add in another order): vLLM's standard, against the exact path on the same inputs, pass by pass: a different pick only at near-ties (top two within 2 bfloat16 steps), under 1% of steps. At the end ≈ the spike's D (real-time factor ~0.33, first audio ~110 ms) or better. **Done 2026-11-17:** stages 1-3 (stage 3 inside our talker loop), plus our own Triton kernels (RMSNorm, rotary + cache write, decode attention) and two CUDA streams: **11.4 ms a frame (Qwen's code: 34.9), RTF 0.17, first audio 20 ms, margin 66 ms**, no stalls (the log below, spikes/01-09). `torch.compile` for contrast and a graph for the codec's decode: later, optional |
| 4.5d ✅ | **speech-to-text** (T12): Qwen3-ASR-0.6B (Apache-2.0, 0.94B parameters, 1.79 GB). 1. **Dependencies:** `qwen-asr` loads beside `qwen-tts` in one environment (PyTorch 2.15 + CUDA 13.2, transformers 4.57.3); if not, stop and rethink. 2. **The spike** (`spikes/10`): accuracy on pi-server's test set (FLEURS English and Chinese, ASCEND mixed; WER, CER, MER), the time from the end of speech to the final text, VRAM with both models loaded, the TTS's frame time and margin while speech-to-text runs on its own stream (barge-in); Whisper large-v3-turbo as a reference point. 3. **The engine:** streaming recognition (partial text while the audio arrives, the final at its end) on our own loop, beside the TTS engine | the dependency check passes; the spike's numbers recorded; GPU tests for the engine; both models loaded with VRAM to spare; the TTS keeps ahead of playback while speech-to-text runs. **Parts 1-2 done 2026-11-17** ([spikes/10](../../python/services/speech-service/spikes/10-stt/)): loads with transformers 4.57.6 (the TTS's codes unchanged); error 3.9% English, 4.2% Chinese, 12.5% mixed (Whisper large-v3-turbo: 24.7% mixed); streaming (Qwen's method: re-feed all the audio each chunk, the text so far as the prefix) 58 ms after the speaker stops; both models 4.7 of 8 GB; realistic barge-in costs neither anything. Decided: 1 s chunks, our own loop on the transformers backend (no vLLM), the ASR's stream at priority 0 (the TTS's decode above). **Part 3 done 2026-11-17:** `app/engine/stt.py` (`QwenSTTEngine`: Qwen's model, one pass on its own stream), `transcription.py` (the streaming logic, pure: Qwen's re-feed with the settled text as the prefix; passes every 1 s to 10 s, every 2 s to 20 s, then every 4 s, so long utterances stay cheap; a burst gets one pass; the 60 s limit), the `STTEngine` interface beside `TTSEngine` (`base.py`; the TTS engine renamed `tts.py`, `QwenTTSEngine`). 10 CPU tests; 8 GPU tests with no test data (the TTS speaks, the STT hears it back): whole utterances equal Qwen's own `transcribe`, English and Chinese heard right, partials then the final in under 0.3 s, silence empty, a forced language, 30 s streamed with every pass inside its interval. All 93 tests pass in one image (transformers 4.57.6) |
| 4.5e 🟡 | **batches of 2, both models** (continuous batching: T13). **Step 1 done** ([spikes/11](../../python/services/speech-service/spikes/11-batching/)): the TTS's generation costs ×1.07-1.08 at batch 2 (batch it); the codec's decode ×1.89 (compute-bound: decoded per stream; a stateful streaming decode later); the STT's passes ×1.64 but at unrelated moments (in turn, not batched). So, for the TTS: a sentence or an utterance **joins between steps and leaves when it ends**, without waiting for the other. Each slot: its own row in the cache, its own position in GPU memory (our kernels read a position per row), its own end check, repetition penalty and sampling; a joiner's prefill runs between two steps; graphs for batch 2 (an empty slot ignored); the codec's decode batched too | each row's results match batch 1's (the near-tie standard); 2 TTS + 2 speech-to-text streams at once stay ahead of real time; measured: a joiner's first audio, the running stream's margin while one joins |
| 4.5f | **the service: sessions and the API** (T13). **Sessions** (requests; async, one per connection) apart from **work** (one thread owns the GPU and runs the batches; sessions only exchange queues with it). Per kind (TTS, speech-to-text): **2 running (the batch) + 1 waiting = 3 accepted; a 4th is refused at once** with `503` and `Retry-After`. Both kinds are WebSocket sessions (T13; the API above): a TTS session for one reply, its text streamed in; a speech-to-text session for one utterance; `POST /v1/audio/speech` as a one-shot TTS session. Released by the stream's end, cancel or disconnect, idle, too long, or waiting too long. Session states `queued → running → done / cancelled / failed / timed out`; `/v1/status` lists slots and sessions; healthy only once warm; **no database**. Tests against a fake engine | tests: streaming order; a 4th session refused at the handshake, the waiting one starts when a slot frees; another caller can't take a slot between a reply's sentences; each release (end, cancel, disconnect, idle, too long, queue timeout) frees the slot; partial then final text; not healthy until warm |
| 4.6 | **deploy:** the manifest, the hook (both models' weights fetched and checked), by hand, then the pipeline; the status page; a smoke test that synthesizes a sentence and transcribes a clip | deployed by the pipeline; the smoke test hears audio (non-empty, the right rate) and reads back the clip's text |
| 4.7 | **measured:** first packet (median of 20 short sentences), the real-time factor, VRAM, the time from start to warm; speech-to-text's latency and error rates; 2 + 2 streams at once; voices for Chinese and English chosen; recorded here | **the goal:** packets stream; the first within the 300 ms target (or a revised one, with the reason); no load time between requests; a restart warm before healthy |

**4.5c log** (each result in [spikes/](../../python/services/speech-service/spikes/), with its code and raw output):
- **Step 2, our own predictor loop** ([02](../../python/services/speech-service/spikes/02-predictor-loop/)): Qwen's
  exact codes; 31.1 ms a frame (from 35.2), first audio 149 ms (from 165). Hugging Face's fixed cache drifts
  (another attention kernel, via its mask), so step 3 uses our own attention on fixed slices.
- **Step 3, graph-ready** ([02](../../python/services/speech-service/spikes/02-predictor-loop/)): our own fixed
  cache, read in fixed slices, no mask: Qwen's exact codes again, and 27.0 ms a frame (RTF 0.38) before
  any graph.
- **Step 4, the CUDA graph** ([02](../../python/services/speech-service/spikes/02-predictor-loop/)): the 15
  passes replayed as one graph: exact codes, **20.0 ms a frame** (−42% from Qwen's `generate`), RTF
  **0.29**, already past the 4.5a spike's D (0.33). With 1, 1, 2, 4: first audio 45 ms, margin 56 ms
  ([03](../../python/services/speech-service/spikes/03-chunk-schedule/)). Stage 1 done.
- **Where a frame goes** ([04](../../python/services/speech-service/spikes/04-frame-split/)): predictor 11.5 ms,
  talker 7.7, loop 0.7; the GPU busy ~16 of 20 ms, with 6,658 tiny kernels a frame. Graphing the talker
  would win ~3 ms; fusing kernels more, so fusion first.
- **A fused RMSNorm in Triton** ([05](../../python/services/speech-service/spikes/05-fused-rmsnorm/)): 8 kernels
  → 1, the predictor's kernels 4,665 → ~2,460: **18.3 ms a frame**, RTF 0.27, first audio 43 ms. Not
  bit-exact (1 element in 735,000 one bfloat16 step off), so fused paths are tested by the near-tie
  standard above: 6 different picks in 2,790 steps, all near-ties.
- **The rotary embedding and the cache write as one kernel** ([06](../../python/services/speech-service/spikes/06-fused-rope/)):
  14 kernels → 1, **bit-exact** once the compiler's FMA contraction is off (it rounds once where PyTorch
  rounds twice). **17.0 ms a frame**, RTF 0.25; with 1, 1, 2, 4: first audio 39 ms, margin 60 ms. The
  predictor's GPU time 11.8 → 8.5 ms, 83% of it matrix multiplies (the weights' floor: ~5.2 ms): the
  talker is the larger target now.
- **Stage 2, step 2: our own talker loop** ([07](../../python/services/speech-service/spikes/07-talker-loop/)):
  Qwen's `generate` replaced by our loop over Qwen's modules on our fixed cache, with its generation rules
  (repetition penalty, suppressed control codes, minimum length) as GPU operations: Qwen's exact codes,
  **12.9 ms a frame** (from 17.0), RTF 0.21. Next: a decode-attention kernel reading the length from GPU
  memory (option c, agreed), then the talker's graph.
- **Steps 3-4: decode attention and the talker's graph** ([08](../../python/services/speech-service/spikes/08-talker-graph/)):
  our online-softmax kernel reads the length from GPU memory, so one graph serves every frame. Fused
  talker **11.4 ms a frame**, RTF **0.18** (Qwen's code: 34.8). The graph itself bought ~nothing: the frame
  is GPU-bound now (busy 90%).
- **Streams and events** ([09](../../python/services/speech-service/spikes/09-streams/)): decode and
  generation shared one CUDA stream, and the stream thread's end check waited for all of it. Now
  generation on its own stream, the decode on a higher-priority one, each frame handed over with a CUDA
  event (a GPU-side wait): **first audio 42 → 20 ms, margin 47 → 66 ms**, RTF 0.17.
- **The chunk schedule** ([03](../../python/services/speech-service/spikes/03-chunk-schedule/)): decode after 1, 1, 2,
  then every 4 frames: first audio **52 ms** (from 143) at the same cost (RTF 0.41), 43 ms of margin
  before a gap. The standard from here on.
- **Found on the way:** greedy, some sentences never end (11 minutes of audio); each sentence is now
  capped at 50 + 8 frames a character.

**Then, later in CP4** (sub-checkpoints defined when reached): text fed into Qwen3-TTS **as it arrives**
within a session (its "trailing text" input, which we now fill all at once), so the voice flows across
sentences: a spike after 4.5e; the
voice path (Hey Pi → pi-server → agent-service, its reply's sentences → speech-service → the speaker);
protocol B in shared-schemas; the agent's latency levers (a faster model for `voice`, the session
started as you begin talking); barge-in end to end.

## Not chosen
| Option | Why not |
|---|---|
| A VM with the card passed through | heavier, and the card would belong to that one VM; LXC is enough for trusted code |
| Loading the model per request (or per session) | seconds of load each time: the opposite of the goal |
| Weights baked into the image | GBs more per image, rebuilt with every code change; pinned weights on disk are as reviewable |
| A cloud TTS (OpenAI, others) | the GPU is here, local speech costs nothing per use, and audio stays home |
| Encoding to Opus or MP3 on the server | an encoder adds latency to the first packet; PCM over the LAN is cheap. A compressed format can come with the phone (over the internet) |

## Resolved in review (2026-11-17)
1. **Speech-to-text:** ~~its own container, sharing the GPU (one service per container)~~. Revised the
   same day: **in this service, in the same process** (T12).
2. **Voices:** the preset voices (`CustomVoice`) first; cloning from a reference clip (`Base`) later.
3. **The first-packet budget:** 300 ms (median, short sentences) as the target; confirmed or changed
   by 4.7's measurement.
