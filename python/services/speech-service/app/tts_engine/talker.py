"""The talker's loop, our own (speech-service.md T11, 4.5c stage 2): what Hugging Face's `generate` did for
the talker, over Qwen's own modules, with our fixed cache (layers.py).

Qwen's `Qwen3TTSForConditionalGeneration.generate` builds the prompt (the text, the speaker, the control
codes) and calls `self.talker.generate(...)`. The engine puts `TalkerLoop.generate` there instead:

    prefill   the prompt (L positions) → 28 layers → codec_head → code 1 of frame 1
    frame t   the code predictor: [talker's last hidden state, embed(code 1)] → codes 2-16   (emit the frame)
              input = Σ the frame's 16 code embeddings + text hidden t (or the pad embedding)
              → 28 layers at position L+t → codec_head → code 1 of frame t+1
    until     code 1 is the end-of-speech code, or the frame limit

Choosing code 1, the rules Qwen passes to `generate`, as GPU tensor operations in its order:
    repetition penalty   codes already used in this sentence: score ÷ 1.05 (× 1.05 if negative)
    suppressed codes     2048-3071 (control codes) never, except end-of-speech
    minimum length       no end-of-speech in the first 2 frames
    then sampling        temperature, top-k, top-p, a draw (`pick`), or the best (greedy)

One sentence at a time (batch 1): the positions are then simply the cache positions (Qwen's multimodal
rotary positions are all equal for speech, layers.rotary_table).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.tts_engine.predictor import pick


class TalkerLoop:
    def __init__(self, talker, *, capacity: int = 2048, fused: bool = False, graph: bool = False):
        """`talker`: Qwen's talker. `capacity`: cache positions (prompt + frames); ~115 KB a position,
        so 2,048 is ~235 MB. `fused`: our kernels. `graph` (needs `fused`): each frame's step (28 layers
        and codec_head) captured once as a CUDA graph and replayed at every position."""
        if graph and not fused:
            raise ValueError("only the fused talker can be a graph: its shapes are the same at every position")
        self.talker, self.capacity, self.fused, self.graph = talker, capacity, fused, graph
        self._buffers: SimpleNamespace | None = None

    def generate(self, *, inputs_embeds, attention_mask, trailing_text_hidden, tts_pad_embed, predictor, emit,
                 max_new_tokens: int, min_new_tokens: int, eos_token_id: int, suppress_tokens,
                 repetition_penalty: float, do_sample: bool, top_k=None, top_p=None, temperature=None,
                 subtalker_dosample: bool = False, subtalker_top_k=None, subtalker_top_p=None,
                 subtalker_temperature=None, forced=None, trace=None, **_unused):
        """Stands in for `talker.generate(...)`. `predictor`: the code predictor to use (a callable like
        `code_predictor.generate`). `emit(codes)`: called with each frame's 16 codes, (batch, 16).
        Returns what Qwen's code reads after `generate`: `.hidden_states`, one entry per forward,
        `((hidden,), codes)`, the prefill's codes None.

        For comparing two talkers step by step (tests): `forced`, a list of frames (1, 16), used in place
        of its own picks and of the predictor (then the end code after the last); `trace`, a list that
        gets each step's code-1 scores after Qwen's rules, (1, vocab) float32."""
        import torch

        if inputs_embeds.shape[0] != 1 or not bool(attention_mask.all()):
            raise ValueError("one sentence at a time, without padding")
        prompt = inputs_embeds.shape[1]
        if prompt + max_new_tokens > self.capacity:
            raise ValueError(f"the prompt and frame limit ({prompt} + {max_new_tokens}) exceed the cache "
                             f"({self.capacity} positions)")
        talker, model = self.talker, self.talker.model
        cp = talker.code_predictor
        b = self._setup(inputs_embeds)
        vocab = talker.config.vocab_size
        suppressed = torch.zeros(vocab, dtype=torch.bool, device=inputs_embeds.device)
        suppressed[list(suppress_tokens)] = True
        seen = torch.zeros((1, vocab), dtype=torch.bool, device=inputs_embeds.device)
        eos_column = torch.zeros(vocab, dtype=torch.bool, device=inputs_embeds.device)
        eos_column[eos_token_id] = True

        def choose(logits, made: int):
            """Code 1 from `logits` (1, vocab) float32, after `made` codes: Qwen's rules, then sampling."""
            penalized = torch.where(logits < 0, logits * repetition_penalty, logits / repetition_penalty)
            logits = torch.where(seen, penalized, logits)
            logits = logits.masked_fill(suppressed, float("-inf"))
            if made < min_new_tokens:
                logits = logits.masked_fill(eos_column, float("-inf"))
            if trace is not None:
                trace.append(logits)
            if forced is not None:
                code = forced[made][:, :1] if made < len(forced) else torch.full_like(seen[:, :1], eos_token_id,
                                                                                      dtype=torch.long)
            else:
                code = pick(logits, do_sample=do_sample, top_k=top_k, top_p=top_p, temperature=temperature)
            seen.scatter_(1, code, True)
            return code                                           # (1, 1)

        sub = dict(do_sample=subtalker_dosample, top_k=subtalker_top_k, top_p=subtalker_top_p,
                   temperature=subtalker_temperature)
        layers = lambda h, start, n: self._layers(h, start, n, b)  # noqa: E731
        # Prefill: the prompt. codec_head over all its positions, then the last, like Qwen's forward
        # (the same matrix shapes, so the same kernels and rounding).
        hidden = layers(inputs_embeds, 0, prompt)
        record = [((hidden[:, -1:],), None)]
        code = choose(talker.codec_head(hidden)[:, -1].float(), 0)
        hidden, made = hidden[:, -1:], 1
        while made < max_new_tokens and int(code) != eos_token_id:     # one sync a frame: the end check
            code_embed = model.codec_embedding(code)                    # (1, 1, hidden)
            if forced is not None:
                rest = SimpleNamespace(sequences=forced[made - 1][:, 1:])
            else:
                rest = predictor(inputs_embeds=torch.cat((hidden, code_embed), dim=1), max_new_tokens=15,
                                 output_hidden_states=True, return_dict_in_generate=True, **sub)  # (Qwen's)
            codes = torch.cat((code, rest.sequences), dim=-1)           # (1, 16): the frame
            emit(codes)
            embeds = torch.cat([code_embed] + [cp.get_input_embeddings()[i](rest.sequences[..., i:i + 1])
                                               for i in range(cp.config.num_code_groups - 1)], dim=1)
            x = embeds.sum(1, keepdim=True)                             # Qwen's sum, in its dtype
            step = made - 1                                             # Qwen's generation_step
            x = x + (trailing_text_hidden[:, step].unsqueeze(1) if step < trailing_text_hidden.shape[1]
                     else tts_pad_embed)
            hidden, logits = self._step(x, prompt + step, b)
            record.append(((hidden,), codes))
            code = choose(logits, made)
            made += 1
        return SimpleNamespace(hidden_states=record)

    def _step(self, x, position: int, b: SimpleNamespace):
        """One frame's talker step at `position`: (hidden (1, 1, hidden), code 1's scores (1, vocab) float32).
        As a graph: the position written into GPU memory and the input copied into the graph's buffer,
        then one replay of ~550 kernels (captured the first time, as in predictor.GraphCodePredictor)."""
        if not self.graph:
            hidden = self._layers(x, position, 1, b)
            return hidden, self.talker.codec_head(hidden)[:, -1].float()
        b.position.fill_(position)
        if b.graph is None:
            self._capture(x, b)
        b.x.copy_(x)
        b.cuda_graph.replay()
        return b.hidden.clone(), b.logits.clone()                # the next replay overwrites them

    def _capture(self, x, b: SimpleNamespace) -> None:
        import torch

        from app.tts_engine.layers import decoder_layers

        model, b.x = self.talker.model, x.clone()

        def run():
            hidden = decoder_layers(model.layers, model.norm, b.x, 0, 1, b.keys, b.values, b.cos, b.sin,
                                    fused=True, position=b.position)
            return hidden, self.talker.codec_head(hidden)[:, -1].float()

        side = torch.cuda.Stream()                               # warm up outside the recording (these
        side.wait_stream(torch.cuda.current_stream())            # runs write this position's keys and
        with torch.cuda.stream(side):                            # values: the same ones, each time)
            for _ in range(3):
                run()
        torch.cuda.current_stream().wait_stream(side)
        b.cuda_graph = torch.cuda.CUDAGraph()
        # thread_local: only this thread is checked while recording; the stream's thread may be decoding
        # audio on the GPU at the same time (the first capture happens during the first sentence).
        with torch.cuda.graph(b.cuda_graph, capture_error_mode="thread_local"):   # recorded, not run
            b.hidden, b.logits = run()
        b.graph = True

    def _setup(self, x) -> SimpleNamespace:
        import torch

        from app.tts_engine.layers import new_cache, rotary_table

        if self._buffers is not None:
            return self._buffers
        model, config = self.talker.model, self.talker.config
        keys, values = new_cache(len(model.layers), 1, config.num_key_value_heads, self.capacity,
                                 model.layers[0].self_attn.head_dim, dtype=x.dtype, device=x.device)
        cos, sin = rotary_table(model.rotary_emb, x, self.capacity, streams=3)
        position = torch.zeros(1, dtype=torch.int32, device=x.device)  # fused: the position, in GPU memory
        self._buffers = SimpleNamespace(keys=keys, values=values, cos=cos, sin=sin, position=position, graph=None)
        return self._buffers

    def _layers(self, h, start: int, n: int, b: SimpleNamespace):
        """The 28 layers. Fused, one position: the position goes through GPU memory (our rotary/cache
        and decode-attention kernels read it), the shape every frame is the same, so step 4's graph
        can replay it at any position."""
        from app.tts_engine.layers import decoder_layers

        model, position = self.talker.model, None
        if self.fused and n == 1:
            position = b.position
            position.fill_(start)
        return decoder_layers(model.layers, model.norm, h, start, n, b.keys, b.values, b.cos, b.sin,
                              fused=self.fused, position=position)
