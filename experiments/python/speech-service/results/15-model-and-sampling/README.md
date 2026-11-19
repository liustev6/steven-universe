# 15: the 1.7B model, the talker's sampling, and Qwen's own path (2026-11-18)

**Question.** Spike 14: the TTS stretches short sentences, pauses, and babbles, mostly with ryan
(Hey Pi's voice). Does lower sampling temperature / top-k fix it? Does the 1.7B model (same 9
voices; it also takes a style instruction, the 0.6B doesn't)? Does it fit beside speech-to-text
with 2 + 2 streams on the 8 GB GPU? And is it the model, or our engine (the same sentences through
Qwen's own `generate`)?

**How.** Two maintenance windows on CT 108 (`run.sh 1`, `run.sh 2`: the live service stopped,
10 and 8 minutes). Each variant ran as the real service (the image, the dev code, its settings as
`SPEECH_*` variables) in the live one's place, measured by spike 14's `rate.py` through the test
client (ryan and serena, 4 runs of 11 sentences: duration, pauses, babble heard back by
speech-to-text, first audio, real-time factor). `reference.py`: Qwen's `generate_custom_voice`, 6
runs, the same sentences. `e2e.py`: 2 TTS + 2 STT at once, then a third and fourth session.
The instruction: "Speak at a steady, natural pace, without long pauses. 用平稳自然的语速说话，不要拖长或停顿。"

## Results
| variant | clips > 1.4x median | babble / empty | ryan: longest pause | "好的。" / "Sure." (ryan, median) | first audio | RTF | VRAM |
|---|---|---|---|---|---|---|---|
| 0.6B, Qwen's sampling (spike 14) | 9 / 66 (ryan) | 6 / 66 (ryan) | 1.52 s | 2.76 / 2.00 s | 28 ms | 0.19 | 4.8 GB |
| 0.6B, temperature 0.7, top-k 20 | 6 / 88 | not kept | 2.32 s | 1.76 / 1.60 s | 28 ms | 0.19 | 4.8 GB |
| 0.6B, temperature 0.6, top-k 10 | 4 / 88 | not kept | 1.32 s; "It's Tuesday." 10.8 s | 1.88 / 1.60 s | 28 ms | 0.19 | 4.8 GB |
| 0.6B, language Auto | 3 / 88 | 1 / 88 | 2.64 s (an 11.8 s clip) | 1.72 / 1.64 s | 28 ms | 0.19 | 4.8 GB |
| 1.7B | 6 / 88 | not kept | 1.08 s; a 20.7 s clip | 0.64 / 0.60 s | 33 ms | 0.25 | 6.7 GB |
| 1.7B + instruction | 5 / 88 | not kept | 0.86 s | 0.68 / 0.88 s | 33 ms | 0.25 | 6.7 GB |
| 1.7B, temperature 0.7, top-k 20 | 10 / 88 | not kept | 0.88 s | 0.68 / 0.76 s | 33 ms | 0.25 | 6.7 GB |
| **1.7B + instruction, language Auto** | **5 / 88** | **0 / 88** | **1.02 s** | **0.64 / 0.72 s** | **33 ms** | **0.25** | **6.7 GB** |

(Window 1's variants ran with the voice's own language as the default, as spike 14; window 2's with
`Auto`, now the default.)

- **It's the model, not our engine.** Qwen's own path stretches ryan as often: 7 of 66 runs over
  1.4x their median ("今天是星期二。" 5.4 s, a 3.4-unit sentence 8.6 s), ours 9 of 66 (6 runs
  each: within noise); serena as steady on both.
- **Lower temperature doesn't fix it,** on either model: fewer small wobbles, the same rare
  runaways (0.6B at 0.6/10: a 10.8 s "It's Tuesday."), and 0.7/20 made the 1.7B worse.
- **The 1.7B speaks short sentences at their natural length** (ryan's "好的。" 0.64 s against
  2.76 s; serena's sentences shorter too: less silence at the ends); **with the instruction it had
  no runaways and no babble** (0 of 88 heard; no pause over 1.02 s).
- **It fits, and keeps up, with 2 + 2:** two TTS streams' first audio 37 and 103 ms, two STT finals
  109 and 61 ms after the commit (heard exactly), the third session queued, the fourth refused
  (503); VRAM 6.96 of 8.15 GB under it (1.2 GB spare). First audio +5 ms; RTF 0.25 (4x real time).

## Decided (2026-11-18)
The live service moves to **Qwen3-TTS-12Hz-1.7B-CustomVoice** (revision `0c0e305`) with the
instruction (`SPEECH_TTS_INSTRUCT`), Qwen's sampling, language `Auto` by default.
