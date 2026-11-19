# speech-service experiments

Measuring tools for speech-service's models, kept out of the service's code (docs/playground.md). Each came
from a spike whose write-up and numbers are in [results/](results/) (one folder per spike).

| Tool | What it measures | From |
|---|---|---|
| `evenness.py` | how evenly each voice speaks: pace, pauses, babble (a model, instruction or voice change) | spikes 14-15 |
| `roundtrip.py` | mixed Chinese and English with numbers, spoken by the service and heard back | spike 13 |
| `trace_reply.py` | one reply through the streaming engine, step by step (imports the service's engine) | spike 16 |

`evenness.py` and `roundtrip.py` talk to the live (or dev) service through its test client; `trace_reply.py`
imports the service's engine. On the playground, `run.sh` runs any of them in speech-base (production's stack)
with the service's code at `/src` and production's pinned models (`run.sh models`, as a deploy fetches them):
`run.sh check` (the imports, no GPU), `run.sh trace_reply.py …` (the GPU: speech-service stopped first). The service's own scripts (smoke test, e2e, its GPU tests'
window) stay in `python/services/speech-service/scripts/`.
