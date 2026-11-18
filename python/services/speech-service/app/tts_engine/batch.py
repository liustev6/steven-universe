"""The TTS's batch: several replies generated together, joining and leaving between steps
(continuous batching; speech-service.md T13, 4.5e; spikes/11: a batch of 2 costs ×1.07 one).

Each **slot** is one generation in progress (a whole reply, fed its text as it's written: Qwen's
streaming mode, spikes/16; or one sentence, its text all in the prompt), a row of every batched tensor:

    the cache            its own row of every layer's keys and values (positions 0…its length)
    its position         in GPU memory, one per row: our kernels read their row's (kernels.py)
    code 1 to expand     the code chosen last step, and the talker's hidden state with it
    its rules            its "seen" codes (repetition penalty), its count (no end in the first 2), its limit

    join(prompt)   a prompt (TTSEngine.stream_prompt, or .prompt) into a free slot: its prefill runs
                   alone on its own cache row, between two steps, and picks its first code 1
    feed(slot, x)  more of a streamed reply's text (its tokens' hidden states, one per step to come)
    finish(slot, x)  the end-of-text mark: the reply's text is complete
    step()         one step for every ready row at once: the predictor (codes 2-16 of each row's frame:
                   the frames out), the summed embeddings + each row's next text hidden state, 28 layers
                   at each row's own position, code 1 chosen for each row by Qwen's rules
    leave(slot)    cancel: the slot is free at once

**A streamed row waits** when its next text token hasn't been written yet: it still runs in the
step (one graph at `slots` rows), at its current position, but nothing of it is kept: no frame out,
its position, code, hidden state and seen codes as they were, its cache row's write at that
position overwritten when it runs for real. So the model sees exactly the inputs it would with the
whole text known (spikes/16): one text token per frame, the end mark, then pads.

Every step runs all `slots` rows: a free row computes nothing useful (zeros, at position 0 of its own
row) and costs little (×1.07 a row: the weights are read once for all). So one graph per piece, at
`slots` rows, serves every mix of busy and free. A row's results don't depend on the other rows: each
output row of a matrix multiply and each program of our kernels reads only its own row.

One sync a step: the chosen codes 1 to the CPU, for the end checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from app.tts_engine.predictor import GraphCodePredictor, pick


@dataclass
class Slot:
    prompt_len: int
    trailing: object                      # (1, T, hidden): the text still to speak, one per step
    pad: object                           # (1, 1, hidden): after the text runs out
    max_codes: int                        # the frame limit (tts.max_frames)
    min_codes: int                        # no end-of-speech before this many
    made: int = 1                         # codes 1 chosen so far (the prefill chose the first)
    text_done: bool = True                # a streamed reply's text is complete (its end mark is in `trailing`)
    trace: list | None = field(default=None, repr=False)

    def waiting(self) -> bool:
        """Its next text token isn't here yet (streamed replies only)."""
        return not self.text_done and self.made - 1 >= self.trailing.shape[1]


class Full(Exception):
    """Every slot is busy."""


class TTSBatch:
    def __init__(self, talker, *, slots: int = 2, capacity: int = 2048):
        import torch

        from app.tts_engine.layers import new_cache, rotary_table

        self.talker, self.slots, self.capacity = talker, slots, capacity
        model, config = talker.model, talker.config
        p = next(talker.parameters())
        self.device, self.dtype = p.device, p.dtype
        self.hidden_size, self.vocab = config.hidden_size, config.vocab_size
        self.keys, self.values = new_cache(len(model.layers), slots, config.num_key_value_heads, capacity,
                                           model.layers[0].self_attn.head_dim, dtype=self.dtype, device=self.device)
        self.cos, self.sin = rotary_table(model.rotary_emb, torch.zeros(1, 1, 1, device=self.device, dtype=self.dtype),
                                          capacity, streams=3)
        z = lambda *shape, **kw: torch.zeros(*shape, device=self.device, **kw)  # noqa: E731
        self.positions = z(slots, dtype=torch.int32)
        self.codes = z(slots, 1, dtype=torch.long)                  # each row's code 1 to expand next
        self.hidden = z(slots, 1, self.hidden_size, dtype=self.dtype)
        self.seen = z(slots, self.vocab, dtype=torch.bool)
        self.state: list[Slot | None] = [None] * slots
        self.rules: dict | None = None
        self._predictor = GraphCodePredictor(talker.code_predictor, fused=True)
        self._graph = None

    # ---- joining and leaving ------------------------------------------------------------------------
    def free(self) -> list[int]:
        return [i for i, s in enumerate(self.state) if s is None]

    def ready(self) -> list[int]:
        """The busy slots that can step now (not waiting for text)."""
        return [b for b, s in enumerate(self.state) if s is not None and not s.waiting()]

    def join(self, prompt: dict, *, slot: int | None = None, trace: list | None = None) -> int:
        """A prompt (TTSEngine.stream_prompt or .prompt) into a free slot (`slot`, if given: a reply's
        next generation stays on its row); its slot number. `trace`: a list that gets each step's code-1
        scores after the rules (tests compare against the single sentence)."""
        import torch

        from app.tts_engine.layers import decoder_layers

        free = self.free()
        if not free or (slot is not None and slot not in free):
            raise Full(f"all {self.slots} slots are busy" if not free else f"slot {slot} is busy")
        b, embeds = (slot if slot is not None else free[0]), prompt["inputs_embeds"]
        if embeds.shape[0] != 1 or not bool(prompt["attention_mask"].all()):
            raise ValueError("one sentence a slot, without padding")
        if embeds.shape[1] + prompt["max_new_tokens"] > self.capacity:
            raise ValueError(f"the prompt and frame limit exceed the cache ({self.capacity} positions)")
        self._set_rules(prompt)
        model = self.talker.model
        with torch.inference_mode():
            # The prefill: this row only, positions 0…L-1 of its own cache row (views of the batch's).
            h = decoder_layers(model.layers, model.norm, embeds, 0, embeds.shape[1],
                               [k[b:b + 1] for k in self.keys], [v[b:b + 1] for v in self.values],
                               self.cos, self.sin, fused=True)
            self.seen[b] = False
            new = Slot(prompt_len=embeds.shape[1], trailing=prompt["trailing_text_hidden"],
                       pad=prompt["tts_pad_embed"], max_codes=self._limit(embeds.shape[1], prompt["max_new_tokens"]),
                       min_codes=prompt["min_new_tokens"], text_done=not prompt.get("streaming", False), trace=trace)
            logits = self.talker.codec_head(h)[:, -1].float()
            self.codes[b] = self._choose(logits, self.seen[b:b + 1], [new], made=[0])[0]
            self.hidden[b] = h[0, -1:]
        self.state[b] = new
        return b

    def feed(self, b: int, text_hidden, *, limit: int | None = None) -> None:
        """More of a streamed reply's text: (1, n, hidden), its tokens' hidden states in order.
        `limit`: its frame limit for the text so far (TTSEngine.max_frames), within the cache."""
        import torch

        s = self.state[b]
        if s.text_done:
            raise ValueError("this slot's text is complete")
        s.trailing = torch.cat((s.trailing, text_hidden.to(s.trailing.dtype)), dim=1)
        if limit is not None:
            s.max_codes = self._limit(s.prompt_len, limit)

    def finish(self, b: int, end_hidden) -> None:
        """The end-of-text mark (1, 1, hidden): after it, pads until the model ends the speech."""
        self.feed(b, end_hidden)
        self.state[b].text_done = True

    def _limit(self, prompt_len: int, frames: int) -> int:
        return max(1, min(frames, self.capacity - prompt_len - 1))

    def leave(self, b: int) -> None:
        self.state[b] = None

    # ---- one step for every row ----------------------------------------------------------------------
    def step(self, on_frames=None) -> tuple[dict[int, object], list[int]]:
        """One frame for every busy slot: ({slot: its frame's 16 codes, (16,) on the GPU}, the slots that
        ended this step: their end code came, or their limit; they are free again).

        `on_frames(frames)`: called as soon as the frames exist, before the talker computes the next
        codes 1, so their audio can start decoding meanwhile (as TalkerLoop hands each frame over right
        after the predictor; spikes/11: first audio 38 → ~20 ms). Record a CUDA event there."""
        import torch

        busy = [b for b, s in enumerate(self.state) if s is not None]
        waiting = [b for b in busy if self.state[b].waiting()]
        busy = [b for b in busy if b not in waiting]
        if not busy:
            return {}, []
        cp, model, r = self.talker.code_predictor, self.talker.model, self.rules
        with torch.inference_mode():
            if waiting:                                                          # kept as they are (see above)
                kept = (self.codes[waiting].clone(), self.hidden[waiting].clone(), self.seen[waiting].clone())
            code_embed = model.codec_embedding(self.codes)                       # (slots, 1, hidden)
            rest = self._predictor(torch.cat((self.hidden, code_embed), dim=1), do_sample=r["subtalker_dosample"],
                                   top_k=r["subtalker_top_k"], top_p=r["subtalker_top_p"],
                                   temperature=r["subtalker_temperature"]).sequences   # (slots, 15)
            frames = torch.cat((self.codes, rest), dim=-1)                       # (slots, 16)
            out = {b: frames[b].clone() for b in busy}
            if on_frames is not None:
                on_frames(out)
            embeds = torch.cat([code_embed] + [cp.get_input_embeddings()[i](rest[:, i:i + 1])
                                               for i in range(cp.config.num_code_groups - 1)], dim=1)
            x = embeds.sum(1, keepdim=True)                                      # Qwen's sum, in its dtype
            text = []
            positions = [0] * self.slots
            for b, s in enumerate(self.state):
                step = (s.made - 1) if s else 0                                  # Qwen's generation_step
                if s is None or b in waiting:
                    text.append(torch.zeros(1, 1, self.hidden_size, device=self.device, dtype=self.dtype))
                else:
                    text.append(s.trailing[:, step].unsqueeze(1) if step < s.trailing.shape[1] else s.pad)
                if s is not None:
                    positions[b] = s.prompt_len + step                           # a waiting row: rewritten later
            x = x + torch.cat(text, dim=0)
            self.positions.copy_(torch.tensor(positions, dtype=torch.int32))
            hidden, logits = self._talker_step(x)
            self.hidden.copy_(hidden)
            made = [s.made if s else 0 for s in self.state]
            rows = [s if b not in waiting else None for b, s in enumerate(self.state)]
            self.codes.copy_(self._choose(logits, self.seen, rows, made=made))
            if waiting:
                self.codes[waiting], self.hidden[waiting], self.seen[waiting] = kept
            next_codes = self.codes[:, 0].tolist()                               # the one sync a step
        ended = []
        for b in busy:
            s = self.state[b]
            s.made += 1
            if next_codes[b] == r["eos_token_id"] or s.made > s.max_codes - 1:
                ended.append(b)
                self.state[b] = None
        return out, ended

    # ---- pieces ----------------------------------------------------------------------------------------
    def _set_rules(self, prompt: dict) -> None:
        """The generation rules (Qwen's, the same for every sentence: one set a batch)."""
        import torch

        keys = ("do_sample", "top_k", "top_p", "temperature", "subtalker_dosample", "subtalker_top_k",
                "subtalker_top_p", "subtalker_temperature", "eos_token_id", "repetition_penalty")
        rules = {k: prompt.get(k) for k in keys}
        if self.rules is None:
            self.rules = rules
            self.suppressed = torch.zeros(self.vocab, dtype=torch.bool, device=self.device)
            self.suppressed[list(prompt["suppress_tokens"])] = True
        elif rules != self.rules:
            raise ValueError("one batch, one set of generation rules")

    def _choose(self, logits, seen, slots, *, made):
        """Code 1 for each row: Qwen's rules (repetition penalty, suppressed codes, no end too early),
        then sampling. `seen` rows are updated with the chosen codes."""
        import torch

        r = self.rules
        p = r["repetition_penalty"]
        logits = torch.where(seen, torch.where(logits < 0, logits * p, logits / p), logits)
        logits = logits.masked_fill(self.suppressed, float("-inf"))
        early = [i for i, s in enumerate(slots) if s is not None and made[i] < s.min_codes]
        if early:
            logits[early, r["eos_token_id"]] = float("-inf")
        for i, s in enumerate(slots):
            if s is not None and s.trace is not None:
                s.trace.append(logits[i:i + 1].clone())
        code = pick(logits, do_sample=r["do_sample"], top_k=r["top_k"], top_p=r["top_p"], temperature=r["temperature"])
        seen.scatter_(1, code, True)
        return code

    def _talker_step(self, x):
        """28 layers + codec_head for every row at its own position: a CUDA graph, captured the first time
        (as TalkerLoop's, at `slots` rows)."""
        import torch

        from app.tts_engine.layers import decoder_layers

        if self._graph is None:
            g = SimpleNamespace(x=x.clone())
            model = self.talker.model

            def run():
                h = decoder_layers(model.layers, model.norm, g.x, 0, 1, self.keys, self.values, self.cos, self.sin,
                                   fused=True, position=self.positions)
                return h, self.talker.codec_head(h)[:, -1].float()

            side = torch.cuda.Stream()
            side.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(side):
                for _ in range(3):
                    run()
            torch.cuda.current_stream().wait_stream(side)
            g.graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(g.graph, capture_error_mode="thread_local"):
                g.hidden, g.logits = run()
            self._graph = g
        self._graph.x.copy_(x)
        self._graph.graph.replay()
        return self._graph.hidden.clone(), self._graph.logits.clone()
