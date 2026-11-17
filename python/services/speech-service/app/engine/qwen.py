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

Two CUDA streams (spikes/09): generation runs on its own stream, the codec's decode on another of higher
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

from app.engine.base import Cancelled, Voice
from app.engine.chunker import ChunkDecoder
from app.engine.predictor import CodePredictor, GraphCodePredictor, StaticCodePredictor
from app.engine.talker import TalkerLoop

# Each preset voice's native language (Qwen's README: speakers sound best in their own language).
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


class QwenEngine:
    def __init__(self, model_path: str, *, device: str = "cuda:0", chunks: int | tuple[int, ...] = (1, 1, 2, 4),
                 context: int = 25, predictor: str = "qwen", talker: str = "qwen"):
        self.model_path, self.device, self.chunks, self.context = model_path, device, chunks, context
        self.predictor, self.talker = predictor, talker       # may change between sentences
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
        language = language or NATIVE.get(voice, "Auto")
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
    def official_codes(self, text: str, voice: str, language: str | None = None) -> object:
        """Qwen's own whole-sentence path, without sampling: the codes the engines must reproduce."""
        import torch

        tts = self._tts
        language = language or NATIVE.get(voice, "Auto")
        input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
        with torch.inference_mode():
            codes, _ = self._model.generate(input_ids=input_ids, instruct_ids=[None], languages=[language],
                                            speakers=[voice], non_streaming_mode=True,
                                            **tts._merge_generate_kwargs(do_sample=False, subtalker_dosample=False))
        return codes[0]
