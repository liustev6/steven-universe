# Wake-word spikes: the results
The measurements behind the wake word ([docs/specs/wake-word.md](../../../../docs/specs/wake-word.md): its decisions,
and an appendix with each spike's core numbers). Each folder keeps the write-up (`README.md`: the question, how it
ran, what it says) and its model's table in the common test (`test-<model>.txt`, from `evaluate.py`: the same rows
and cutoffs for every model, so they compare side by side). The code is in [spikes/](../spikes/) (each
spike's own scripts) and beside it (`train.py`, `train-run.sh`, `recording/`); the data (recordings, generated
voices, models) is on the Mac and the playground, never in git.

Spikes 00-02 trained on the Mac (M-series), 03 on and after on the playground (CT 110), its voices generated on the
RTX 5060 with speech-service stopped by hand. All are microWakeWord MixedNet models scored as pi-server runs them
(`pymicro-wakeword` 2.5.0, streaming, a 5-frame average).

| # | Date | Question | Answer |
|---|---|---|---|
| [00](00-hey-pi/) | 2026-11-18 | "Hey Pi" from microWakeWord's recipe on our hardware? | Yes: live since 2026-11-18 at 0.95; misses 24% at 0.4 false wakes/h on its test, but 6.7/h in the owner's room and 35.5/h in everyday Chinese (measured 2026-11-19) |
| [01](01-xiao-pai/) | 2026-11-18 | 小派小派 from our own TTS alone (7 voices)? | Near misses 2%, almost no false wakes, but 46% of unheard voices at 0.95: 5 usable voices are too few speakers |
| [02](02-mandarin-noise/) | 2026-11-18 | + Mandarin meetings (AliMeeting) and household noise (DEMAND, MUSAN)? | Unheard voices 83% at 0.95; 1 false wake/h at 0.97 in 2 h of meetings. Weak: 小爱小爱, 小胖小胖, loud noise |
| [03](03-many-voices/) | 2026-11-19 | + 255 synthetic voices, the owner's voice (Mac mic), short videos? | Unheard voices 90-99% at 0.95; 0 wakes in held-out short videos. Weak: doubled near misses (小怕小怕 ~60%), a lone 小派 |
| [04](04-pi-mic/) | 2026-11-19 | + the owner on the Pi's mic, 25 doubled near misses from every voice; how much background to mix? | Videos mixed under the wake word made it wake to videos (18/h). Without (04b, at 0.90): the owner 92-100%, unheard voices 79-96%, near misses halved, 0 false wakes in the room. Weak: other voices in loud noise. 04d/04e running |
