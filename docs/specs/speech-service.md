# Spec: speech-service (speech out and in, streamed, from models that stay loaded on the GPU)

**Status: live** since 2026-11-18, at `https://speech.lan.liustev6.ca` on CT 108, deployed by the
pipeline. Built in [agent-service](agent-service/README.md)'s CP4 (voice), sub-checkpoints 4.1–4.6
([tracker.md](agent-service/tracker.md)); its first caller beyond tests is Hey Pi
([voice-path.md](voice-path.md)). Related: [lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md),
[boot.md](boot.md), [auth-service.md](auth-service.md), [deployer.md](deployer.md). Code:
[python/services/speech-service](../../python/services/speech-service/). Every measurement behind a
decision is a spike: summarized in the appendix, its write-up and raw results (the metrics) in
[experiments/python/speech-service/results/](../../experiments/python/speech-service/results/).

## What it is
One long-running process on the GPU that turns **text into speech** and **speech into text**, both
**streamed**: audio leaves as it's generated, partial text arrives while someone is still speaking.
Both models are loaded and warmed once at start, before the service reports healthy, and stay in VRAM;
no request ever waits for a model to load.

```
callers (pi-server, Steven AI, smoke tests, the test client)
  └─(token, aud=speech-service)─▶ CT 108 :443
       WS /v1/realtime/speech         a reply's text in ──▶ its audio out (24 kHz PCM, as it's made)
       WS /v1/realtime/transcription  an utterance's audio in ──▶ partial text, then the final
       POST /v1/audio/speech          one-shot speech (OpenAI's shape)
          │ sessions (asyncio) ─queues─▶ one work thread per model ─▶ the GPU (two CUDA streams)
          │   TTS: Qwen3-TTS-12Hz-1.7B-CustomVoice, a batch of 2 replies   STT: Qwen3-ASR-0.6B, a batch of 2
pve01: NVIDIA driver ── /dev/nvidia* ──▶ CT 108 (unprivileged LXC) ──▶ Docker (NVIDIA Container Toolkit)
```

**As deployed** (RTX 5060, 8 GB): a reply spoken as one continuous generation, fed its text as it's
written (T19); first audio ~30 ms after its first piece; generation 4× faster than real time per stream; speech-to-text's final ~60–110 ms after the speaker stops; 2 TTS + 2 STT
streams at once in real time; 6.96 of 8.15 GB VRAM under that load; warm ~20 s after a start.

## Decisions
| ID | Decision | Why |
|---|---|---|
| T1 | **The GPU belongs to an LXC container, CT 108**, not a VM: the driver on pve01, the device nodes given with Proxmox's `devN` entries, the driver's user-space libraries inside, Docker with the NVIDIA Container Toolkit (`no-cgroups = true`). | Lighter than a VM (no second kernel, no reserved memory), and the card can serve more than one container. The isolation is weaker, which is fine for our own code; **agent sandboxes never get the GPU**. |
| T2 | **The GPU serves only voice.** A general GPU service waits for more GPUs. | One GPU, one purpose: no scheduling between unrelated workloads. |
| T3 | **One process holds the models, loaded and warmed at start**, and never unloads them. Warm-up runs a sentence and an utterance through the work threads (capturing the CUDA graphs) before `/health` turns 200. | No request pays a model load; a restart pays it once, at deploy or boot. |
| T4 | **Models are pinned like code:** each repository at one revision, each file's SHA-256 in [models.yaml](../../infra/services/speech-service/models.yaml). The weights live on CT 108's disk, fetched and verified by the deploy's pre-start hook; the service runs offline. Which model runs is `config.env`'s `SPEECH_TTS_MODEL` / `SPEECH_STT_MODEL`; a replaced model stays pinned and on disk. | Weights are a reviewed input like an image. Keeping them out of the image keeps CI's images small. Rolling back is one config line. |
| T5 | **The one-shot API is OpenAI's speech shape:** `POST /v1/audio/speech`, audio streamed back chunked; default raw 16-bit PCM at 24 kHz, or WAV. | Tools that speak OpenAI's TTS API work unchanged; PCM needs no encoder, so the first packet leaves as soon as it's made. |
| T6 | **A cancel or a disconnect stops the work at once** and frees the slot. | Barge-in: when you interrupt, the GPU stops speaking for you immediately. |
| T7 | **Auth as every service:** auth-service tokens, audience `speech-service`, scopes `speech:create` (speak), `speech:transcribe` (hear), `status:read` (the status page). The service has no client of its own: it only checks tokens. | One identity model for the lab; no shared keys. |
| T8 | **A reply streams into one session** as its text is written, and is spoken as one generation (T19); at most 4,096 characters (OpenAI's limit). | Speech starts while the rest is still being written, and no other caller can take the slot mid-reply. |
| T9 | **Deployed like every service:** its manifest, the pipeline, a smoke test that speaks a sentence and hears it back, a token-protected `/v1/status` on the lab's status page. | Nothing new to operate. |
| T10 | **speech-service is an engine; the caller orchestrates.** pi-server turns speech into text, sends it to agent-service, streams the reply's text here, plays the audio, and handles barge-in. speech-service knows nothing of chat. | Each piece does one job; any client (Hey Pi, a phone, a notification) can use speech alone. The cascaded voice-agent design (Pipecat, LiveKit Agents). |
| T11 | **Our own TTS engine on Qwen's official code**, streaming: the talker generates frames (80 ms of audio each), the codec decodes them in chunks of 1, 1, 2, then every 4 frames with 25 frames of context, and only new audio is sent. Speed is ours: our own generation loops on a fixed KV cache, CUDA graphs, Triton kernels (RMSNorm, rotary + cache write, decode attention), and two CUDA streams (the decode above generation). **Correctness standard:** our exact paths reproduce Qwen's codes; fused kernels may differ only at near-ties (vLLM's standard). Each generation is capped at 50 + 8 frames a character of its text (within its row's cache). | Qwen's package doesn't stream (whole sentences: 1.7 s to first audio). Ours: first audio ~20–33 ms, 3× faster frames than Qwen's code. The cap stops a generation that never ends from holding the GPU. Building it was CP4's learning goal. |
| T12 | **Speech-to-text runs in the same process**, Qwen3-ASR-0.6B, streamed: each pass re-reads the whole utterance with the text it's sure of as a prefix (Qwen's method), every 1 s to 10 s, every 2 s to 20 s, then every 4 s; a 60 s limit. The Pi keeps the wake word and voice detection and streams audio here only while someone speaks. | One CUDA context and one scheduler for the GPU: two processes would take turns in time slices, while one process can overlap them on streams (how Riva and Triton host several models). 3.9% / 4.2% / 12.5% errors (English / Chinese / mixed) against Whisper large-v3-turbo's 24.7% mixed. The Pi has no fan and made ~20% errors itself. |
| T13 | **Concurrency: per model, a batch of 2 running + 1 waiting**; a 4th session is refused at the handshake (`503` + `Retry-After`). **A session is one WebSocket**: one reply (TTS) or one utterance (STT). Sessions never touch the GPU: **one work thread per model** owns its batch (continuous batching: streams join and leave between steps) and exchanges queues with them. A session releases its slot by itself: at its end, on cancel or disconnect, when idle (TTS 15 s, STT 10 s), when too long (10 min, 60 s), or after 10 s waiting. **No database:** what was said belongs to the caller. | Two callers at once (Hey Pi, Steven AI) cost ~1.3× one, not 2×: a step's cost is reading the weights, read once for the batch. Refusing fast beats a long queue: a reply that starts seconds late is worse than a retry. Separate threads per model keep an STT pass (~60 ms) from stalling the TTS. |
| T14 | **Text normalization before the TTS:** each sentence's numbers, units, dates and symbols become words ("2026年" → 二零二六年, "5,959 metres" → five thousand nine hundred and fifty nine metres), with wetext (WeTextProcessing's grammars), the grammar picked by the sentence's script (Chinese, English, Japanese, Korean); anything else, or a failure, is spoken as written. Clients still see their own text. | A TTS model guesses at symbols (the same number read four ways); rules read the same text the same way every time. Heard right 56% → 90%. The front end of production TTS (Riva's). |
| T15 | **The TTS model is Qwen3-TTS-12Hz-1.7B-CustomVoice with a steady-pace instruction** ("Speak at a steady, natural pace, without long pauses. 用平稳自然的语速说话，不要拖长或停顿。", `SPEECH_TTS_INSTRUCT`), Qwen's own sampling, and the language `Auto` unless a client names one. The talker's sampling stays configurable (`SPEECH_TTS_TEMPERATURE`, `_TOP_K`, `_TOP_P`, `_REPETITION_PENALTY`). | The 0.6B stretched short sentences, paused for seconds, and babbled, worst with some voices; it's the model (Qwen's own path does the same), and lower temperature doesn't fix it. The 1.7B with the instruction speaks short sentences at their natural length with no runaways or babble, and still fits 2 + 2 beside speech-to-text. Telling a voice its native language when the text is another made it worse: `Auto` lets the model read it. |
| T16 | **A reply's first piece may end at a comma** ("好的，", "Sure, "), fed at once (`SPEECH_TTS_FIRST_CLAUSE`); later pieces end at sentence ends. Within one generation (T19) a piece is only where text is handed over, not a break in the voice. **Off in the live config since 2026-11-18:** with one generation per reply, a short first piece gives the model a frame or two of text, then it waits for the next sentence: a fragment of the first word (the breath of 好), then a gap. Back on once text is fed as it arrives. | The first audio comes after the first clause, not the first whole sentence (a Chinese reply's first audio 325 → 84 ms beside another reply). |
| T17 | **Speech-to-text can end the turn itself** (`?turn_detection=server`): 700 ms of quiet after at least 150 ms of speech (`silence_ms` 300–3000); otherwise the client's `commit` ends it. | A client without its own voice detection (the test client, pi-server today) still gets turns; the threshold is the common default for voice agents. |
| T18 | **Speech-to-text hears with context:** each pass's prompt carries the session's context (`?context=`, at most 1,000 characters: who speaks and in which languages, recent turns, names, places), after the service's own (`SPEECH_STT_CONTEXT`: empty unless a hint is true of every caller), as Qwen3-ASR's system message. pi-server sends "The speaker may speak English, Chinese, or both mixed in one sentence." and the voice session's last two turns. | A short phrase can be heard in the wrong language: "Seattle downtown" on its own came back as 死丫头当场 (Chinese, sounds alike). Context biases the words and the language toward what the conversation is about: the standard remedy (speech adaptation, phrase lists, Whisper's prompt). Who the speakers are is the caller's to say (T10). |
| T19 | **A reply is spoken as one generation, fed its text as it's written** (Qwen's streaming mode): the prompt holds the first piece's first token, and each audio frame's input gets the next text token, one per 80 ms frame, then the end mark when the reply is complete. The session's splitter still cuts the text into pieces (the first clause, then sentences), each normalized and fed as it completes. A row whose next token isn't written yet **waits** (it skips the step; nothing of it is kept). A reply too long for one row's cache (2,048 positions, ~160 s) continues in a new generation on the same row after ~90 s of text. | Sentence by sentence, the voice reset at every sentence (tone, pace, pitch drawn afresh), each ended as if it were the whole message, and the gap between two was only the silence the clips carried (0.08–0.18 s whatever the punctuation). In one generation the model places its own pauses and speaks the reply as one passage, and reads more faithfully (spike 16). Waiting for text gives the model exactly the inputs it would have with the whole text known, so a live reply sounds as the spike's; generation is 4× faster than playback, so short waits aren't heard. |

## API (443)
| Route | Scope | What |
|---|---|---|
| `WS /v1/realtime/speech` `?voice=…&language=…` | `speech:create` | a TTS session for one reply (`language`: none means `Auto`). Client: `{type: text, text}` (as it's written), `{type: end}` (the reply is complete), `{type: cancel}`. Server: `queued` {position}, `started` {format: `pcm_s16le`, sample_rate: 24000, channels: 1}; `sentence` {index, text} as each piece is accepted for speaking; the reply's audio as binary frames (raw PCM, one continuous voice: T19, playable as it arrives); at the end `done` {samples}, `cancelled`, or `error` {code: idle (no text and nothing being spoken for 15 s), too_long, queue_timeout, bad_message, failed}. Refused at the handshake with an HTTP status and a problem body (401/403, 400 unknown voice, 503 + Retry-After warming or full) |
| `WS /v1/realtime/transcription` `?language=…&turn_detection=server&silence_ms=…` | `speech:transcribe` | a speech-to-text session for one utterance (OpenAI Realtime-shaped). Client: binary audio (16 kHz 16-bit PCM, ~100 ms each, starting a little before the voice), `{type: commit}`, `{type: cancel}`. Server: `queued`, `started` {format, sample_rate: 16000, channels}; `partial` {text, stable, language}: the whole text so far (`stable`: its start that won't change); `final` {text, language, reason: `commit`, `silence`, `idle`, `too_long`}; or `cancelled`; or `error` {code: idle, queue_timeout, bad_message, failed}. Refused at the handshake as the TTS's |
| `POST /v1/audio/speech` `{input, voice, language?, response_format?: "pcm" \| "wav"}` | `speech:create` | a one-shot TTS session: the whole input as one generation (T19), audio streamed as it's made. Headers `X-Sample-Rate`, `X-Request-Id`. `400` unknown voice or too long; `503` + Retry-After full or warming |
| (both sessions) | | **The close code says why** (RFC 6455): 1000 a normal end, 1008 idle / too long / a bad message, 1011 a failure, 1013 waited too long for a place. A connection that dies silently is caught by pings (every 20 s) and ends as a disconnect |
| `GET /v1/voices` | `speech:create` | the preset voices, each with its native language |
| `GET /v1/status` | `status:read` | `ok`, `warm`, the models, the GPU and its VRAM, per kind the slots (running, waiting), sessions, sentences or finals, recent first-audio and final times |
| `GET /health` | none | `200` once warm; `503` while loading |

## The container (CT 108)
| | |
|---|---|
| name, DNS, alias | `speech-service`, `speech.lan.liustev6.ca` (192.168.50.34), `speech-host` |
| size | 4 cores, 8 GB memory, 64 GB disk (the images, the weights) |
| boot | `onboot`, order 2 (it needs only auth-service, for token keys) |
| GPU | `dev0` … `devN`: `/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools`, `/dev/nvidia-caps/*` |
| the image | `speech-base` (python:3.12-slim + a lock of the tested packages: PyTorch 2.15 with CUDA 13.2, Triton, transformers, qwen-tts, qwen-asr, wetext; built by CI) and the service on it (code only), run as uid 10001, read-only, `/tmp` a tmpfs with exec (Triton) |

**pve01's kernel and the driver:** the driver is a kernel module, built for each Proxmox kernel by DKMS;
pve01 follows Proxmox's kernel updates (not pinned), and `./setup.sh reboot` refuses to reboot into a
kernel the module isn't built for ([lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md)).

## Operating it
- **One copy of the models fits the GPU.** Anything else holding VRAM on CT 108 (a dev container)
  makes a start fail out of memory: stop it before a deploy.
- **GPU work that can't run beside the live service runs in a maintenance window:** the live service
  stopped, the work run in its place (same port, same token), then the live one started again:
  `scripts/gpu_window.sh` (the GPU tests, then the dev code as the service: e2e, a streamed reply).
  Experiments (a new model, a voice change measured with `experiments/python/speech-service/evenness.py`,
  synthetic data) run on the playground (CT 110, [playground.md](../playground.md)), speech-service
  stopped by hand meanwhile.
- **Changing a model:** lock it from a tested copy (`python -m app.models lock`), add it to
  `models.yaml`, place its weights on CT 108 (or let the hook download them), point `config.env` at it.

## Not chosen
| Option | Why not |
|---|---|
| A VM with the card passed through | heavier, and the card would belong to that one VM; LXC is enough for trusted code |
| Loading a model per request | seconds of load each time: the opposite of the goal |
| Weights baked into the image | GBs more per image, rebuilt with every code change; pinned weights on disk are as reviewable |
| A cloud TTS or STT | the GPU is here, local speech costs nothing per use, and audio stays home |
| Encoding to Opus or MP3 on the server | an encoder delays the first packet; PCM over the LAN is cheap (a compressed format can come with the phone, over the internet) |
| Qwen's official package, or `faster-qwen3-tts` | the package doesn't stream; the wrapper broke on a current `transformers` |
| Speech-to-text in its own container, or on the Pi | two processes can't overlap on one GPU; the Pi throttles and errs ~20% |
| Whisper large-v3-turbo | 24.7% errors on mixed Chinese/English against Qwen3-ASR's 12.5% |
| A request per sentence | the slot could be lost mid-reply, and each sentence would pay a new prefill |
| Lower sampling temperature to steady the TTS | fewer small wobbles, the same rare runaways (spike 15) |
| Greedy decoding | some sentences never end; fine only for tests that compare exact codes |

## Appendix: the spikes
Every measurement behind a decision. Each spike's write-up and raw results (the metrics) are kept in
`experiments/python/speech-service/results/`; their code was removed once decided (2026-11-18; recover it with
`git log --diff-filter=D -- python/services/speech-service/spikes`). The measuring tools still in use are in
`experiments/python/speech-service/` (since 2026-11-19): `evenness.py` (pace, pauses, babble per voice),
`roundtrip.py` (spoken, then heard back), `trace_reply.py` (one reply, step by step); the service keeps
`scripts/gpu_window.sh` (its GPU tests) and `scripts/e2e.py`. `scripts/bench.py` and `scripts/chunks.py`
(spikes 02-09) were removed then, their numbers in the spikes' write-ups. All on CT 108's RTX 5060 (8 GB), PyTorch 2.15 with CUDA 13.2.
Spikes 01–14 used the TTS model Qwen3-TTS-12Hz-0.6B-CustomVoice; 15 compared it with the 1.7B; 16 ran on the 1.7B.
A frame is 80 ms of audio. RTF (real-time factor) is generation time ÷ audio length: below 1 is faster
than speech. First audio is the time from a sentence's text to its first audio packet. Margin is how
far generation stays ahead of playback (a negative margin is a gap you hear).

### 01: which engine? (2026-11-16)
Qwen's official package, our own streaming loop, batching, or CUDA graphs.

| Run | RTF (median) | First audio (median / worst) | Peak VRAM |
|---|---|---|---|
| A: the official package, whole sentences | 0.47 | 1,735 / 4,177 ms | 2.5 GB |
| B: our streaming loop, plain PyTorch, 4-frame chunks | 0.51 | 169 / 173 ms | 2.5 GB |
| B with 12-frame chunks | 0.47 | 457 ms | |
| C: a batch of 2 vs one at a time | throughput ×1.75–1.85 (similar lengths), ×1.03 (different) | | |
| D: CUDA graphs (the `faster-qwen3-tts` wrapper), 4-frame chunks | 0.33 | 112 / 179 ms | 2.3 GB |

Streaming makes the first audio ~10× earlier at the same speed. CUDA graphs cut a frame's work from
~40 to ~27 ms. Decided: our own streaming engine, with our own graphs.

### 02: can our own code-predictor loop match Qwen's exactly?
| Predictor | Codes vs Qwen's | ms a frame | First audio | RTF |
|---|---|---|---|---|
| Qwen's `generate` | the reference | 35.2 | 165 ms | 0.49 |
| our loop, Hugging Face's growing cache | identical | 31.1 | 149 ms | 0.43 |
| our loop, Hugging Face's fixed cache | differ (44 of 1,320 steps, all near-ties) | 37.6 | 174 ms | 0.51 |
| our loop, our own fixed cache read in fixed slices | identical | 27.0 | | 0.38 |
| the same as a CUDA graph | identical | 20.0 | | 0.29 |

Hugging Face's fixed cache drifts because its mask selects another attention kernel, which rounds
differently. Our own cache, read without a mask, stays exact and can be captured in a graph.
Found on the way: with greedy decoding some sentences never end, so each sentence is capped at
50 + 8 frames a character.

### 03: how small can the first audio chunk be?
| Decode schedule (frames) | First audio | RTF | Stalls | Margin (worst) |
|---|---|---|---|---|
| 4 | 143 ms | 0.41 | none | 193 ms |
| 1, 2, 4 | 52 ms | 0.41 | none (once 123 ms) | 13 ms |
| **1, 1, 2, 4** | **52 ms** | **0.41** | **none** | **43 ms** |
| 1, 1, 2, 2, 4 | 52 ms | 0.41 | one of 108 ms | −108 ms |

Each frame not waited for saves ~30 ms of first audio. The second chunk is the tightest moment, so it
stays small. Decided: 1, 1, 2, then every 4 frames, each with 25 frames of context.

### 04: where does a frame's time go?
| Part of a frame | Time (median) |
|---|---|
| a frame, end to end | 19.9 ms |
| the predictor's graph replay | 11.5 ms |
| the talker's own work | 7.7 ms |
| between calls (the loop) | 0.7 ms |
| the prompt's prefill | 8.0 ms |
| GPU busy | ~16 of 20 ms; 6,658 kernels a frame |

Each kernel costs a few microseconds whatever it does, so 6,658 of them cost ~13 ms. Fusing kernels
would win more than graphing the talker (~3 ms).

### 05: a fused RMSNorm in Triton
| | Kernels a frame (predictor) | GPU time | Engine ms a frame | First audio | RTF |
|---|---|---|---|---|---|
| graph | 4,665 | 11.8 ms | 20.4 | 47 ms | 0.29 |
| graph + fused RMSNorm | ~2,460 | ~9.7 ms | 18.3 | 43 ms | 0.27 |

One kernel instead of 8 per norm: −10% a frame. Not bit-exact: 1 element in 735,000 lands one bfloat16
step off, and 3 of 1,320 steps pick another code, all near-ties. So fused paths are judged by the
near-tie standard (vLLM's), not by whole-sentence equality.

### 06: the rotary embedding and the cache write as one kernel
| Predictor | Kernels a frame | GPU time | Engine ms a frame | First audio (1, 1, 2, 4) | Margin | RTF |
|---|---|---|---|---|---|---|
| graph | 4,665 | 11.8 ms | 20.3 | 46 ms | 56 ms | 0.29 |
| + fused RMSNorm and rotary | ~1,640 | ~8.5 ms | 17.0 | 39 ms | 60 ms | 0.25 |

Bit-exact once the compiler's fused multiply-add is off (it rounds once where PyTorch rounds twice).
The predictor is now 83% matrix multiplies, close to the weights' reading floor; the talker is the
larger target.

### 07: our own talker loop instead of Qwen's `generate`
| Predictor : talker | ms a frame | First audio | RTF |
|---|---|---|---|
| fused : Qwen's | 17.0 | 40 ms | 0.25 |
| fused : ours | 12.9 | 39 ms | 0.21 |

Qwen's exact codes, −24% a frame with no graph: Hugging Face's per-step work (a growing cache, masks,
rotary angles, stop checks) is gone.

### 08: a decode-attention kernel and the talker's graph
| Predictor : talker | ms a frame | First audio | RTF |
|---|---|---|---|
| Qwen's : Qwen's | 34.8 | 57 ms | 0.49 |
| fused : ours | 12.9 | 39 ms | 0.21 |
| fused : fused | 11.5 | 42 ms | 0.18 |
| fused : graph | 11.4 | 43 ms | 0.18 |

Our attention kernel reads the length from GPU memory, so one graph serves every frame. The graph
itself won almost nothing: the GPU is busy 90% of a frame now. Capturing a graph while another thread
uses the GPU needs `capture_error_mode="thread_local"`.

### 09: why didn't faster frames give earlier audio?
| | First audio | Chunk 2 | Chunk 3 | Margin | RTF |
|---|---|---|---|---|---|
| one CUDA stream | 42 ms | 75 ms | 104 ms | 47 ms | 0.18 |
| two streams with events | 20 ms | 33 ms | 57 ms | 66 ms | 0.17 |

Decoding and generation shared one stream, and the end check waited for both. Generation on its own
stream, the decode on a higher-priority one, frames handed over with CUDA events.

### 10: speech-to-text on the same GPU
Errors (%: word error for English, character error for Chinese, mixed error for mixed; 120 clips):

| Mode | English | Chinese | Mixed | RTF | Final after the speaker stops (p50 / p90) |
|---|---|---|---|---|---|
| Qwen3-ASR-0.6B, whole utterance | 3.9 | 4.2 | 12.5 | 0.027 | 177 / 289 ms |
| Qwen3-ASR-0.6B, streaming 1 s chunks | 5.0 | 5.1 | 13.3 | 0.075 | 58 / 67 ms |
| Qwen3-ASR-0.6B, streaming 2 s chunks | 4.5 | 5.0 | 12.9 | 0.051 | 60 / 103 ms |
| Whisper large-v3-turbo, whole utterance | 3.6 | 4.8 | 24.7 | 0.026 | 189 / 219 ms |

Both models in one process:

| While the TTS speaks | TTS first audio | TTS margin (worst) | TTS RTF | STT final |
|---|---|---|---|---|
| the TTS alone | 21 ms | 66 ms | 0.172 | |
| STT whole utterances nonstop (worst case) | 29 ms | 63 ms (60) | 0.241 | 293 ms |
| STT streaming in real time (barge-in) | 20 ms | 66 ms (65) | 0.178 | 59 ms |

Qwen3-ASR halves Whisper's error on mixed speech. Both models fit (4.7 of 8 GB). Listening while
speaking (barge-in) costs the TTS nothing. Streaming re-reads the whole utterance each pass, with the
text so far as a prefix.

### 11: what does a batch of 2 cost?
| Work | Batch 1 | Batch 2 | Batch 4 |
|---|---|---|---|
| the predictor's 15 passes (graph) | 8.41 ms | 9.09 ms (×1.08) | 9.52 ms (×1.13) |
| the talker's step (graph) | 2.89 ms | 3.09 ms (×1.07) | 3.24 ms (×1.12) |
| the codec's decode, batched | 16.98 ms | 32.11 ms (×1.89) | 60.68 ms (×3.57) |
| an STT pass (3 s and 8 s clips), batched | 35.89 ms | 58.95 ms (×1.64) | 80.51 ms (×2.24) |
| an STT pass, one after another | 36.02 ms | 76.07 ms (×2.11) | 152.24 ms (×4.23) |

Generation batches almost for free: each step reads the weights once for the whole batch. The codec's
decode is compute-bound, and 86% of it re-decodes context (a stateful streaming decode would cut it).
STT passes batch somewhat.

### 12: 2 TTS + 2 STT at once, in real time?
| Load | TTS first audio | TTS worst margin | Gaps | STT finals after the speaker stops | VRAM peak |
|---|---|---|---|---|---|
| 2 + 2, staggered | 27, 31 ms | 43 ms | none | 63, 130 ms | 4.24 GB |
| 2 + 2, all at once | 47, 49 ms | 37 ms | none | 73, 95 ms | 4.59 GB |
| 4 + 4, staggered | 29–53 ms | 20 ms | none | 73–153 ms | 5.03 GB |

Every utterance was heard right. One work thread per model: on a single thread an STT pass (~60 ms)
would hold up the TTS's steps for its whole margin. STT finals wait for the next tick, so the service
ticks at once when an utterance ends.

### 13: text normalization before the TTS
Runs heard right, of 6 each (3 by a Chinese voice, 3 by an English one), as written and after
normalization with wetext:

| Sentence | As written | Normalized | What normalization wrote |
|---|---|---|---|
| 今天是2026年11月17日。 | 6 | 6 | 二零二六年十一月十七日 |
| 英国的首都用英文说是London。 | 5 | 6 | unchanged |
| 这个耳机打折后是299元，便宜了15%。 | 5 | 6 | 两百九十九元，百分之十五 |
| 我的电话号码是138-0013-8000。 | 0 | 6 | 一三八零零一三八零零零 |
| 现在气温是-5.5°C，明天会升到3°C。 | 3 | 5 | 负五点五摄氏度 |
| 我昨天用iPhone 17给Steven发了一条message。 | 6 | 6 | iPhone 十七 |
| The meeting is at 3:30 PM on March 5th, 2026. | 2 | 4 | three thirty PM on the fifth of march, twenty twenty six |
| Mount Logan is 5,959 metres tall, or 19,551 feet. | 0 | 4 | five thousand nine hundred and fifty nine metres … |
| **all** | **27 / 48 (56%)** | **43 / 48 (90%)** | |

English words inside Chinese ("London", "iPhone") were never the problem; numbers and symbols were.
Normalization takes ~1–2 ms a sentence. Known misses: a bare "m" read as minutes, "$12.50" as
"twelve point five dollars".

### 14: why do some short sentences take so long?
11 short replies (Chinese, English, mixed), each spoken 4–6 times per voice, measured from the audio
and heard back:

| Voice | Worst / median duration | Clips over 1.4× their median | Longest pause | Silence before / after | Babble heard |
|---|---|---|---|---|---|
| serena | 1.16× | 0 / 110 | 0.66 s | 0.22 / 0.30 s | 0 |
| vivian | 1.28× | 0 / 44 | 1.22 s | 0.48 / 0.48 s | 0 |
| dylan | 1.41× | 1 / 44 | 1.58 s | 0.48 / 0.52 s | 0 |
| eric | 2.10× | 4 / 44 | 0.60 s | 0.09 / 0.18 s | 0 |
| uncle_fu | 2.55× | 3 / 44 | 1.96 s | 0.46 / 0.55 s | 0 |
| aiden | 4.09× | 1 / 44 | 1.64 s | 0.22 / 0.26 s | 0 |
| ryan | 3.56× | 9 / 66 | 1.52 s | 0.45 / 0.55 s | 4 ("哈哈哈哈哈哈！好的", "嗯嗯嗯嗯嗯") |

The 0.6B stretched short sentences, paused and babbled, depending on the voice ("好的。" by ryan:
1.5–5.8 s). Every sentence carried ~0.5 s of silence at each end. The codec's chunks don't change a
sentence's length: its length is its frame count. Speaking rate when it goes right: Chinese 4.7
characters a second, English 3.1 syllables a second.

### 15: the 1.7B model, the sampling, and Qwen's own path (2026-11-18)
ryan and serena, 4 runs of spike 14's 11 sentences per variant, each variant run as the real service:

| Variant | Clips over 1.4× median | Babble | ryan's longest pause | ryan's "好的。" / "Sure." | First audio | RTF | VRAM |
|---|---|---|---|---|---|---|---|
| 0.6B, Qwen's sampling (spike 14) | 9 / 66 (ryan) | 6 / 66 (ryan) | 1.52 s | 2.76 / 2.00 s | 28 ms | 0.19 | 4.8 GB |
| 0.6B, temperature 0.7, top-k 20 | 6 / 88 | | 2.32 s | 1.76 / 1.60 s | 28 ms | 0.19 | 4.8 GB |
| 0.6B, temperature 0.6, top-k 10 | 4 / 88 | | 1.32 s (one 10.8 s clip) | 1.88 / 1.60 s | 28 ms | 0.19 | 4.8 GB |
| 0.6B, language Auto | 3 / 88 | 1 / 88 | 2.64 s (one 11.8 s clip) | 1.72 / 1.64 s | 28 ms | 0.19 | 4.8 GB |
| 1.7B | 6 / 88 | | 1.08 s (one 20.7 s clip) | 0.64 / 0.60 s | 33 ms | 0.25 | 6.7 GB |
| 1.7B + instruction | 5 / 88 | | 0.86 s | 0.68 / 0.88 s | 33 ms | 0.25 | 6.7 GB |
| 1.7B, temperature 0.7, top-k 20 | 10 / 88 | | 0.88 s | 0.68 / 0.76 s | 33 ms | 0.25 | 6.7 GB |
| **1.7B + instruction, language Auto** | **5 / 88** | **0 / 88** | **1.02 s** | **0.64 / 0.72 s** | **33 ms** | **0.25** | **6.7 GB** |

2 TTS + 2 STT at once on the chosen setup: TTS first audio 37 and 103 ms, STT finals 109 and 61 ms
(heard exactly), the third session queued, the fourth refused; VRAM 6.96 of 8.15 GB.

Qwen's own `generate` stretched ryan as often as ours (7 of 66 runs over 1.4×), so it's the model, not
our engine. Lower temperature didn't fix it. The 1.7B with a steady-pace instruction speaks short
sentences at their natural length with no runaways and no babble. Decided: the 1.7B (T15).

### 16: one continuous generation per reply, the text fed as it comes (2026-11-18)
Today a reply is spoken as separate sentences, each its own generation. Qwen3-TTS can instead read the
text while it speaks (its streaming mode: one text token added to each 80 ms frame's input). The 1.7B,
the live settings, through Qwen's own `generate`:

| Text | Mode | Pauses: longest, total | Heard alike | Gaps where pieces join |
|---|---|---|---|---|
| English story, ryan, ~75 s | sentences (today) | 1.18 s, 11.8 s | 0.99 | 0.08–0.18 s at every join |
| | whole text in the prompt | 0.74 s, 13.6 s | 0.88–0.93 | |
| | streaming, one token a frame | 0.82 s, 11.3 s | 0.99–1.00 | |
| Chinese story, dylan, ~37 s | sentences (today) | 0.78 s, 4.7 s | 0.99 | 0.10–0.18 s |
| | streaming | 0.72–1.42 s, 8.5–9.8 s | 0.99 | |
| Mixed reply, dylan, ~13 s | sentences (today) | 0.40 s, 1.5 s | 0.99 ("记得在 umbrella") | 0.10–0.14 s |
| | streaming | 0.80 s, 3.5 s | 1.00 ("记得带 umbrella。") | |

Today's joins are the same short gap whatever the punctuation, which is what sounds wrong. In one
generation the model places its own pauses and speaks the reply as one passage, with no reset of tone
between sentences. A reply fed live waits for its next token when the agent hasn't written it, so the
model sees the same inputs as with the whole text: the service would say exactly this. Decided: T19,
live since 2026-11-18 (its GPU tests: a reply fed in pieces with waits is bit-identical to the same text
fed at once, greedy; each step within the near-tie standard of Qwen's own streaming mode).

