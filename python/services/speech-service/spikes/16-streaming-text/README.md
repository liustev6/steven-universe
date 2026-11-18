# 16: one continuous generation per reply, the text fed as it comes (2026-11-18)

**Question.** A reply spoken as separate sentences sounds wrong at the joins, and two sentences can
sound in different tones: each sentence is its own generation (its tone, pace and pitch drawn afresh,
ending as if it were the whole message), and the pause between two is only the silence the two clips
happen to carry. Qwen3-TTS can read text while it speaks (its streaming mode). Does one generation per
reply, fed one text token per frame, sound right?

**How Qwen's streaming mode works** (`modeling_qwen3_tts.py`, `generate(non_streaming_mode=False)`):
the prompt holds the first text token; then each audio frame's input is the frame's codes plus the
next text token's embedding, one per 80 ms frame; after the last, the end-of-text mark, then a pad
every frame until the model ends the speech. The text runs ahead of the voice and the model aligns
them. **Fed live:** a reply whose next token hasn't been written yet waits (its row skips the step);
the model then sees exactly the inputs it would with the whole text known, so this spike's output is
what the service would say. Generation runs 4x faster than playback, so short waits aren't heard.

**How.** `compare.py`, in a maintenance window (`run.sh`): the 1.7B with the live service's settings
(the steady-pace instruction, normalization, Qwen's sampling), 3 texts, 3 modes, 2 runs, through
Qwen's own `generate` (our engine reproduces its codes). Measured from the audio: pauses of 150 ms or
more, the gaps at the joins (today's mode), how alike its speech-to-text is to the text.

    sentences   today: split as the service splits, each piece generated alone, played back to back
    whole       one generation, all the text in the prompt (non-streaming mode)
    streaming   one generation, the text fed one token per frame (streaming mode)

## Results
| Text | Mode | Audio | Pauses (longest, total) | Heard alike | Joins between pieces |
|---|---|---|---|---|---|
| English story, ryan (962 characters, 14 pieces) | sentences | 80.5, 72.4 s | 1.18 s, 11.8 s; 0.70 s, 5.5 s | 0.99, 0.05 (its STT failed: 72 s) | 0.08–0.18 s, every one |
| | whole | 78.6, 81.8 s | 0.74 s, 13.6 s; 0.88 s, 14.5 s | 0.93, 0.88 | |
| | **streaming** | 68.1, 77.0 s | 0.82 s, 11.3 s; 0.74 s, 11.9 s | **1.00, 0.99** | |
| Chinese story, dylan (162 characters, 7 pieces) | sentences | 34.3, 33.8 s | 0.78 s, 4.7 s; 0.58 s, 3.8 s | 0.99, 0.99 | 0.10–0.18 s |
| | whole | 39.6, 41.9 s | 0.96 s, 9.2 s; 1.20 s, 10.2 s | 1.00, 0.99 | |
| | **streaming** | 40.4, 37.8 s | 1.42 s, 9.8 s; 0.72 s, 8.5 s | 0.99, 0.99 | |
| Mixed reply, dylan (80 characters, 3 pieces) | sentences | 12.0, 12.2 s | 0.40 s, 1.5 s; 0.48 s, 1.7 s | 0.99, 1.00 ("记得**在** umbrella") | 0.10–0.14 s |
| | whole | 12.6, 12.9 s | 0.60 s, 2.5 s; 0.68 s, 2.6 s | 1.00, 0.94 | |
| | **streaming** | 14.6, 14.2 s | 0.80 s, 3.5 s; 0.72 s, 2.7 s | 1.00, 1.00 ("记得带 umbrella。") | |

- **Today's joins are what sounds wrong:** 0.08–0.18 s at every boundary, whatever the punctuation:
  a sentence's end gets the same gap as a comma's, shorter than speech pauses at a full stop.
- **In one generation the model places its own pauses** where the sense and punctuation call for
  them (about twice the total pause time of today's mode, none over 1.4 s), and speaks the reply as one
  passage: no reset of tone between sentences (by ear: the WAVs).
- **Streaming read the text most faithfully** (0.99–1.00); a sentence split mid-thought by today's
  first-clause cut was misspoken ("记得在 umbrella" for "记得带umbrella，").
- **No runaways** in any mode, on long text (75 s) included; Qwen's own code made it at RTF ~0.5
  (our engine: ~0.25).

## Proposed (after listening to the WAVs)
The TTS speaks a reply as **one generation fed the text as it's written** (Qwen's streaming mode):
each batch row holds a reply, not a sentence; a row waits (skips the step) when its next text token
hasn't arrived; normalization still runs per sentence as each completes, before its tokens are fed.
The engine work: the next step.
