"""The plain engine: Qwen's own model code, streamed (speech-service.md T11). The reference every faster
engine (CUDA graphs, 4.5c) is checked against, code for code.

How it streams, without changing Qwen's code:

    1. Qwen's `model.generate` (Hugging Face generation) runs in a background thread.
    2. A PyTorch forward hook on the talker catches each step's output: the 16 codes of one frame
       (Qwen's talker returns them as `hidden_states[1]`).
    3. Each frame goes to the ChunkDecoder, which turns each chunk of frames (1, 1, 2, then every 4)
       into new audio.

Qwen's own `generate_custom_voice` does the same work but decodes only once the sentence is complete.
Cancelling: the hook raises `Cancelled` at the next frame, which stops generation in its thread.

Two CUDA streams (spike 09): generation runs on its own stream, the codec's decode on another of higher
priority, so the GPU runs them side by side and the decode goes first when both wait. Each frame crosses
with a CUDA event, recorded on the generation stream when its codes are ready, waited on by the
consumer's stream on the GPU: the CPU never waits for generation work queued after the frame. The end
code is checked in the generation thread (ours knows it; Qwen's hook reads it there), not here.

The code predictor (the 15 codes after each frame's first, predictor.py): `predictor` picks whose loop
runs it. "qwen" is Hugging Face's `generate`, as Qwen ships it; "ours" is our own loop over the same
modules (step 2); "ours-static" our own forward over the same modules, with a fixed 16-position cache
read in fixed slices (graph-ready, step 3); "ours-graph" that loop as a CUDA graph (step 4);
"ours-fused" the graph with our own fused kernels (kernels.py).

The talker (each frame's first code, talker.py): `talker` picks whose loop runs it. "qwen" is Hugging
Face's `generate`; "ours" our own loop over Qwen's modules with our fixed cache (stage 2), which hands
each frame to the stream itself (the forward hook then has nothing to catch); "ours-fused" with our
kernels (fused norms, rotary/cache write, decode attention reading the position from GPU memory);
"ours-graph" that, each frame's step replayed as one CUDA graph.
"""

from __future__ import annotations

import functools
import queue
import threading
from collections.abc import Iterator

import numpy as np

from app.tts_engine.base import Cancelled, Voice
from app.tts_engine.chunker import ChunkDecoder
from app.tts_engine.predictor import CodePredictor, GraphCodePredictor, StaticCodePredictor
from app.tts_engine.talker import TalkerLoop

# Each preset voice's native language (Qwen's README: speakers sound best in their own language).
# Each voice's native language: what /v1/voices reports. Not the default for speaking: that is "Auto"
# (Qwen's), since a voice reading another language with its own language set stretches and babbles more
# (spike 14: ryan on "好的。" with English set, median 2.76 s; with Auto, 1.48 s).
NATIVE = {"vivian": "Chinese", "serena": "Chinese", "uncle_fu": "Chinese", "dylan": "Chinese",
          "eric": "Chinese", "ryan": "English", "aiden": "English", "ono_anna": "Japanese", "sohee": "Korean"}
_DONE = object()
PREDICTORS = ("qwen", "ours", "ours-static", "ours-graph", "ours-fused")
TALKERS = ("qwen", "ours", "ours-fused", "ours-graph")


def max_frames(text: str) -> int:
    """A sentence's frame limit. Some sentences never produce the end code (greedy "Warming up." runs to
    Qwen's 8,192 frames, 11 minutes), and the one GPU worker would be held for minutes. Speech needs at
    most ~3-4 frames a character (Chinese: 3.2; English: under 1), so this is generous."""
    return 50 + 8 * len(text)


class QwenTTSEngine:
    def __init__(self, model_path: str, *, device: str = "cuda:0", chunks: int | tuple[int, ...] = (1, 1, 2, 4),
                 context: int = 25, predictor: str = "qwen", talker: str = "qwen",
                 sampling: dict | None = None, instruct: str | None = None):
        """`sampling`: the talker's settings over the model's own (temperature, top_k, top_p,
        repetition_penalty: they decide each frame's first code, so the timing; spike 14); None or
        missing keys keep Qwen's. `instruct`: a style instruction for every sentence ("speak at a steady
        pace"), for models that take one (1.7B; Qwen ignores it on the 0.6B)."""
        self.model_path, self.device, self.chunks, self.context = model_path, device, chunks, context
        self.predictor, self.talker = predictor, talker       # may change between sentences
        self.sampling = {k: v for k, v in (sampling or {}).items() if v is not None}
        unknown = set(self.sampling) - {"temperature", "top_k", "top_p", "repetition_penalty"}
        if unknown:
            raise ValueError(f"unknown sampling settings: {sorted(unknown)}")
        self.instruct = instruct or None
        self.sample_rate = 24000
        self._tts = None

    # ---- start-up --------------------------------------------------------------------------------
    def load(self) -> None:
        import torch
        from qwen_tts import Qwen3TTSModel

        self._tts = Qwen3TTSModel.from_pretrained(self.model_path, device_map=self.device, dtype=torch.bfloat16,
                                                  attn_implementation="sdpa")
        model = self._tts.model
        self._model, self._talker, self._codec = model, model.talker, model.speech_tokenizer
        self._eos = model.config.talker_config.codec_eos_token_id
        cp = self._talker.code_predictor
        self._predictors = {"ours": CodePredictor(cp), "ours-static": StaticCodePredictor(cp),
                            "ours-graph": GraphCodePredictor(cp),
                            "ours-fused": GraphCodePredictor(cp, fused=True)}
        self._talkers = {"ours": TalkerLoop(self._talker), "ours-fused": TalkerLoop(self._talker, fused=True),
                         "ours-graph": TalkerLoop(self._talker, fused=True, graph=True)}
        self._generation = torch.cuda.Stream(device=self.device)
        self._decoding = torch.cuda.Stream(device=self.device, priority=-1)   # negative: higher priority
        # sample_rate stays 24,000: the 12 Hz codec's output_sample_rate (its config); the GPU tests check
        # it against what the codec's own decode reports.

    def warm_up(self) -> None:
        """One throwaway sentence, as requests run (sampled): CUDA starts up (the first generation takes
        ~10 s, the rest don't). A sentence that ends: greedy "Warming up." never does."""
        for _ in self.stream("Hi, how can I help you today?", "ryan"):
            pass

    def voices(self) -> list[Voice]:
        return [Voice(name, NATIVE.get(name, "Auto")) for name in (self._tts.get_supported_speakers() or [])]

    # ---- one sentence ----------------------------------------------------------------------------
    def frames(self, text: str, voice: str, language: str | None = None, *, sample: bool = True,
               cancel: threading.Event | None = None) -> Iterator[object]:
        """Each frame's 16 codes (a tensor) as the model makes it, until the end of the sentence."""
        import torch

        tts, got = self._tts, queue.Queue()
        language = language or "Auto"                       # as Qwen: the model reads it from the text
        tts._validate_languages([language])
        tts._validate_speakers([voice])

        def emit(codes):                                      # one frame's codes, (1, 16): in the
            if cancel is not None and cancel.is_set():        # generation thread, on its stream
                raise Cancelled("cancelled")
            frame, ready = codes[0].detach().clone(), torch.cuda.Event()
            ready.record()                                    # …marks where the frame's codes are done
            got.put((frame, ready))

        def hook(module, args, out):                          # each step of Qwen's talker: one frame
            codes = out.hidden_states[1]
            if codes is not None and int(codes[0, 0]) != self._eos:   # (generate syncs each step anyway)
                emit(codes)
            elif cancel is not None and cancel.is_set():
                raise Cancelled("cancelled")

        input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
        kwargs = tts._merge_generate_kwargs(do_sample=sample, subtalker_dosample=sample,
                                            max_new_tokens=max_frames(text))
        errors: list[BaseException] = []

        def run():
            try:
                with torch.inference_mode(), torch.cuda.stream(self._generation):
                    self._model.generate(input_ids=input_ids, instruct_ids=[None], languages=[language],
                                         speakers=[voice], non_streaming_mode=True, **kwargs)
            except BaseException as exc:                      # Cancelled, or a real failure
                errors.append(exc)
            finally:
                got.put(_DONE)

        if self.predictor not in PREDICTORS or self.talker not in TALKERS:
            raise ValueError(f"predictor must be one of {PREDICTORS}, talker one of {TALKERS}")
        cp = self._talker.code_predictor
        if self.predictor != "qwen":                          # an instance attribute shadows the method
            cp.generate = self._predictors[self.predictor]
        if self.talker != "qwen":
            self._talker.generate = functools.partial(self._talkers[self.talker].generate,
                                                      predictor=cp.generate, emit=emit)
        handle = self._talker.register_forward_hook(hook)
        self._generation.wait_stream(torch.cuda.current_stream())   # the prompt's tensors, made here
        worker = threading.Thread(target=run, name="speech-generate", daemon=True)
        worker.start()
        try:
            while (item := got.get()) is not _DONE:
                frame, ready = item
                here = torch.cuda.current_stream()
                here.wait_event(ready)                        # on the GPU: no CPU wait
                frame.record_stream(here)                     # its memory: in use on this stream too
                yield frame
        finally:
            worker.join()
            handle.remove()
            cp.__dict__.pop("generate", None)                 # Qwen's own again (official_codes uses it)
            self._talker.__dict__.pop("generate", None)
        if errors:
            raise errors[0]

    def prompt(self, text: str, voice: str, language: str | None = None, *, sample: bool = True,
               streaming: bool = False) -> dict:
        """One sentence's prompt, as Qwen's code builds it, without generating: what it would pass to
        `talker.generate` (the prompt's embeddings, the text still to speak, the pad embedding, the
        generation rules). The batch (batch.py) joins sentences from these. Qwen's `generate` builds them
        and calls the talker; a stand-in there captures its arguments and stops it. `streaming`: Qwen's
        streaming mode (see stream_prompt)."""
        import torch

        tts = self._tts
        language = language or "Auto"                       # as Qwen: the model reads it from the text
        tts._validate_languages([language])
        tts._validate_speakers([voice])
        input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
        kwargs = tts._merge_generate_kwargs(do_sample=sample, subtalker_dosample=sample,
                                            max_new_tokens=max_frames(text), **self.sampling)
        instruct_ids = [None]
        if self.instruct and getattr(self._model, "tts_model_size", "") not in "0b6":   # as Qwen: not on the 0.6B
            instruct_ids = [tts._tokenize_texts([tts._build_instruct_text(self.instruct)])[0]]

        class Captured(Exception):
            pass

        def capture(**got):
            raise Captured(got)

        self._talker.generate = capture
        try:
            with torch.inference_mode():
                self._model.generate(input_ids=input_ids, instruct_ids=instruct_ids, languages=[language],
                                     speakers=[voice], non_streaming_mode=not streaming, **kwargs)
        except Captured as c:
            return c.args[0]
        finally:
            self._talker.__dict__.pop("generate", None)
        raise RuntimeError("Qwen's generate never called the talker")

    def stream_prompt(self, text: str, voice: str, language: str | None = None, *, sample: bool = True) -> dict:
        """A reply's prompt in Qwen's streaming mode (spike 16): the prompt holds `text`'s first token,
        `trailing_text_hidden` the rest, one per step to come; more text comes with `text_hidden`, the
        end with `end_hidden` (TTSBatch.feed, .finish). Qwen ends the trailing text with the end mark;
        here it's left off until the reply is complete."""
        got = self.prompt(text, voice, language, sample=sample, streaming=True)
        got["trailing_text_hidden"] = got["trailing_text_hidden"][:, :-1]          # its end mark: later
        got["streaming"] = True
        return got

    def text_hidden(self, text: str):
        """More of a reply's text as the talker reads it: its tokens' hidden states, (1, n, hidden), as
        Qwen's streaming mode builds `trailing_text_hidden` (the text embedding, then its projection).
        `text` carries its own leading space where one belongs (an English word after another)."""
        import torch

        ids = self._tts._tokenize_texts([text])[0].to(self._talker.device)
        with torch.inference_mode():
            return self._talker.text_projection(self._talker.get_text_embeddings()(ids.reshape(1, -1)))

    def end_hidden(self):
        """The end-of-text mark's hidden state, (1, 1, hidden)."""
        import torch

        ids = torch.tensor([[self._model.config.tts_eos_token_id]], device=self._talker.device)
        with torch.inference_mode():
            return self._talker.text_projection(self._talker.get_text_embeddings()(ids))

    def batch(self, slots: int = 2, *, capacity: int = 2048):
        """A batch of `slots` sentences generated together (batch.py): fused kernels, CUDA graphs."""
        from app.tts_engine.batch import TTSBatch

        return TTSBatch(self._talker, slots=slots, capacity=capacity)

    def stream(self, text: str, voice: str, language: str | None = None, *, sample: bool = True,
               cancel: threading.Event | None = None) -> Iterator[np.ndarray]:
        import torch

        def decode(frames) -> np.ndarray:
            with torch.inference_mode():
                wavs, _ = self._codec.decode([{"audio_codes": torch.stack(list(frames))}])
            return wavs[0]                                    # on the CPU: waits for this stream only

        chunker = ChunkDecoder(decode, chunks=self.chunks, context=self.context)
        with torch.cuda.stream(self._decoding):               # frames wait for their events here
            for frame in self.frames(text, voice, language, sample=sample, cancel=cancel):
                if (audio := chunker.add(frame)) is not None:
                    yield audio
            if (audio := chunker.flush()) is not None:
                yield audio

    # ---- the reference, for comparisons -------------------------------------------------------------
    def official_codes(self, text: str, voice: str, language: str | None = None, *, streaming: bool = False) -> object:
        """Qwen's own path, without sampling: the codes the engines must reproduce (`streaming`: its
        streaming mode, the text fed one token a step)."""
        import torch

        tts = self._tts
        language = language or "Auto"                       # as Qwen: the model reads it from the text
        input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
        with torch.inference_mode():
            codes, _ = self._model.generate(input_ids=input_ids, instruct_ids=[None], languages=[language],
                                            speakers=[voice], non_streaming_mode=not streaming,
                                            **tts._merge_generate_kwargs(do_sample=False, subtalker_dosample=False))
        return codes[0]
