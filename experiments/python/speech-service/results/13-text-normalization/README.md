# 13: text normalization before the TTS (2026-11-17)

**Question.** The TTS misreads digits ("5,959" → "five, varying nine hundred…"), and in mixed
Chinese/English it isn't clear which language a number is read in ("今天是2026年"). Does rewriting
numbers, units and symbols as words before the TTS (text normalization, TN) fix it, and with what?

**How.** `roundtrip.py`: 8 sentences (Chinese with years, money, percentages, a phone number,
temperatures, English words inside Chinese; English with times, dates, thousands), each spoken 3 times
by a Chinese voice (serena) and an English one (ryan) through the live service, then heard back by its
speech-to-text, so how each was read shows as text. Once as written (`results-before.txt`), once
through `normalize.py` first (`results-after.txt`). TN: **wetext 0.2.1** (WeTextProcessing's runtime:
its grammars compiled to FSTs, run with kaldifst; Apache-2.0), language `auto` (per sentence: the
Chinese grammar when it has Chinese, else English).

## Results
Runs heard right (of 6 per sentence; "right" = the transcript reads the sentence as meant):

| # | Sentence | Before | After TN | What TN wrote |
|---|---|---|---|---|
| 1 | 今天是2026年11月17日。 | 6 | 6 | 二零二六年十一月十七日 |
| 2 | 英国的首都用英文说是London。 | 5 | 6 | unchanged: TN only touches numbers and symbols |
| 3 | 这个耳机打折后是299元，便宜了15%。 | 5 | 6 | 两百九十九元，百分之十五 |
| 4 | 我的电话号码是138-0013-8000。 | **0** | 6 | 一三八零零一三八零零零 (a digit dropped or 八千 before) |
| 5 | 现在气温是-5.5°C，明天会升到3°C。 | 3 | 5 | 负五点五摄氏度 (ryan said 负点五 all 3 times before) |
| 6 | 我昨天用iPhone 17给Steven发了一条message。 | 6 | 6 | iPhone 十七 |
| 7 | The meeting is at 3:30 PM on March 5th, 2026. | 2 | 4 | three thirty PM on the fifth of march, twenty twenty six |
| 8 | Mount Logan is 5,959 metres tall, or 19,551 feet. | **0** | 4 | five thousand nine hundred and fifty nine … |
| | **all** | **27 / 48 (56%)** | **43 / 48 (90%)** | |

- **Code-switching is not the problem:** "London" inside Chinese, "iPhone", "Steven", "message"
  were spoken right every time. The model mixes languages well; it's *symbols* it guesses at.
- **Numbers are:** thousands separators, phone numbers, negative decimals; and a Chinese voice
  reading an English sentence read its numbers in Chinese, then babbled (up to 10 s). After TN, the
  same voice reads the English sentences right.
- **What's left is the sampling** (not TN's): a stray "哼" or "Or, or", a word order swapped
  ("the March of fifth"), and some runs far too long for their words (15.4 s, 19.4 s): the
  open "segments too long" item, its own fix (sampling settings, a length cap per character).
- **Cost:** ~1–2 ms a sentence; the first sentence in each language loads its grammar (16–90 ms:
  warm it at start-up). Size: the wheel is 35 MB, 294 MB installed (20 languages' grammars);
  Chinese + English + the shared processors are 52 MB. kaldifst ships manylinux_2_28 wheels
  (x86_64 and aarch64), Python 3.12.
- **TN's own misses seen:** "$12.50" → "twelve point five dollars"; "March 5th" → "the fifth
  of march" (British order, fine). A sentence mixing an English clause and a Chinese clause gets
  one grammar (auto picks per input): rare in a voice reply; splitting by script would fix it.

## Decided
TN in speech-service, before each sentence's prompt (any client gets it): wetext, `auto` per
sentence, warmed at start-up, on by default (`SPEECH_TTS_NORMALIZE`). Only the en/zh grammars kept
in the image. Then the sampling's babble, separately.
