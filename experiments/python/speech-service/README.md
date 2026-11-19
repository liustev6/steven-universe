# speech-service experiments

Measuring tools for speech-service's models, kept out of the service's code (docs/playground.md). Each came
from a spike whose write-up and numbers are in `docs/specs/speech-service-spikes/`.

| Tool | What it measures | From |
|---|---|---|
| `evenness.py` | how evenly each voice speaks: pace, pauses, babble (a model, instruction or voice change) | spikes 14-15 |
| `roundtrip.py` | mixed Chinese and English with numbers, spoken by the service and heard back | spike 13 |
| `trace_reply.py` | one reply through the streaming engine, step by step (imports the service's engine) | spike 16 |

`evenness.py` and `roundtrip.py` talk to the live (or dev) service through its test client; `trace_reply.py`
runs on the GPU with the service's code mounted. The service's own scripts (smoke test, e2e, its GPU tests'
window) stay in `python/services/speech-service/scripts/`.
