"""The plain engine: Qwen's own model code, streamed (tts-service.md T11). The reference every faster
engine (CUDA graphs, 4.5c) is checked against, code for code.

How it streams, without changing Qwen's code:

    1. Qwen's `model.generate` (Hugging Face generation) runs in a background thread.
    2. A PyTorch forward hook on the talker catches each step's output: the 16 codes of one frame
       (Qwen's talker returns them as `hidden_states[1]`).
    3. Each frame goes to the ChunkDecoder, which turns every `chunk` frames into new audio.

Qwen's own `generate_custom_voice` does the same work but decodes only once the sentence is complete.
Cancelling: the hook raises `Cancelled` at the next frame, which stops generation in its thread.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator

import numpy as np

from app.engine.base import Cancelled, Voice
from app.engine.chunker import ChunkDecoder

# Each preset voice's native language (Qwen's README: speakers sound best in their own language).
NATIVE = {"vivian": "Chinese", "serena": "Chinese", "uncle_fu": "Chinese", "dylan": "Chinese",
          "eric": "Chinese", "ryan": "English", "aiden": "English", "ono_anna": "Japanese", "sohee": "Korean"}
_DONE = object()


class QwenEngine:
    def __init__(self, model_path: str, *, device: str = "cuda:0", chunk: int = 4, context: int = 25):
        self.model_path, self.device, self.chunk, self.context = model_path, device, chunk, context
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
        # sample_rate stays 24,000: the 12 Hz codec's output_sample_rate (its config); the GPU tests check
        # it against what the codec's own decode reports.

    def warm_up(self) -> None:
        """One throwaway sentence: CUDA starts up (the first generation takes ~10 s, the rest don't)."""
        for _ in self.stream("Warming up.", "ryan", sample=False):
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

        def hook(module, args, out):                          # each talker step: one frame's codes
            if cancel is not None and cancel.is_set():
                raise Cancelled("cancelled")
            codes = out.hidden_states[1]
            if codes is not None:
                got.put(codes[0].detach().clone())

        input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
        kwargs = tts._merge_generate_kwargs(do_sample=sample, subtalker_dosample=sample)
        errors: list[BaseException] = []

        def run():
            try:
                with torch.inference_mode():
                    self._model.generate(input_ids=input_ids, instruct_ids=[None], languages=[language],
                                         speakers=[voice], non_streaming_mode=True, **kwargs)
            except BaseException as exc:                      # Cancelled, or a real failure
                errors.append(exc)
            finally:
                got.put(_DONE)

        handle = self._talker.register_forward_hook(hook)
        worker = threading.Thread(target=run, name="tts-generate", daemon=True)
        worker.start()
        try:
            while (item := got.get()) is not _DONE:
                if int(item[0]) == self._eos:                 # the end-of-speech code: not audio
                    continue
                yield item
        finally:
            worker.join()
            handle.remove()
        if errors:
            raise errors[0]

    def stream(self, text: str, voice: str, language: str | None = None, *, sample: bool = True,
               cancel: threading.Event | None = None) -> Iterator[np.ndarray]:
        import torch

        def decode(frames) -> np.ndarray:
            with torch.inference_mode():
                wavs, _ = self._codec.decode([{"audio_codes": torch.stack(list(frames))}])
            return wavs[0]

        chunker = ChunkDecoder(decode, chunk=self.chunk, context=self.context)
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
