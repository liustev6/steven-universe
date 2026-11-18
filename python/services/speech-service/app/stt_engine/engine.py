"""The speech-to-text engine: Qwen3-ASR-0.6B, streamed (speech-service.md T12, 4.5d; spikes/10).

Qwen's model and processor (`qwen-asr`, Apache-2.0), on the transformers backend; the streaming is
ours (transcription.py: Qwen's method, our schedule), as the TTS's loop is (app/tts_engine/talker.py). Qwen's own
streaming needs vLLM; this doesn't.

One decode pass = the processor turns the audio and the prompt (+ the text so far) into the model's
input, `generate` writes the rest greedily (Qwen's streaming uses temperature 0). Passes run on the
engine's own CUDA stream at normal priority: level with the TTS's generation, below its audio decode
(spike 10: playback has the hard deadline, speech-to-text a soft one).
"""

from __future__ import annotations

import numpy as np

from app.stt_engine.base import Transcript
from app.stt_engine.transcription import SAMPLE_RATE, StreamingTranscription


class QwenSTTEngine:
    sample_rate = SAMPLE_RATE

    def __init__(self, model_path: str, *, device: str = "cuda:0", priority: int = 0, max_seconds: float = 60.0,
                 max_new_tokens: int = 512):
        """`max_new_tokens`: the text's limit a pass (Qwen's default; ~60 s of fast Chinese fits)."""
        self.model_path, self.device, self.priority = model_path, device, priority
        self.max_seconds, self.max_new_tokens = max_seconds, max_new_tokens
        self._asr = None

    def load(self) -> None:
        import torch
        from qwen_asr import Qwen3ASRModel
        from qwen_asr.inference.utils import parse_asr_output

        self._asr = Qwen3ASRModel.from_pretrained(self.model_path, dtype=torch.bfloat16, device_map=self.device,
                                                  attn_implementation="sdpa", max_new_tokens=self.max_new_tokens)
        self._processor, self._model = self._asr.processor, self._asr.model
        self._parse_raw = parse_asr_output
        self._stream = torch.cuda.Stream(device=self.device, priority=self.priority)

    def warm_up(self) -> None:
        """A pass on a second of quiet noise: CUDA and the kernels start up before the service is healthy."""
        self.transcribe(np.random.default_rng(0).normal(0, 1e-3, SAMPLE_RATE).astype(np.float32))

    def languages(self) -> list[str]:
        return list(self._asr.get_supported_languages())

    def session(self, language: str | None = None, *, decode="own") -> StreamingTranscription:
        """One utterance. `decode=None`: no decode of its own (a batch runs its passes: batch.py)."""
        prompt, forced = self._prompt(language), self._language(language)
        own = (lambda audio, prefix: self.decode_batch([audio], [prompt + prefix], [prefix])[0]) if decode else None
        return StreamingTranscription(own, self._processor.tokenizer,
                                      lambda raw: self._parse_raw(raw, user_language=forced), max_seconds=self.max_seconds)

    def prompt(self, language: str | None = None) -> str:
        """Qwen's prompt; with a language, it asks for that language's text only."""
        return self._prompt(language)

    def batch(self, slots: int = 2, **kw):
        """Several utterances sharing passes (batch.py)."""
        from app.stt_engine.batch import STTBatch

        return STTBatch(self, slots=slots, **kw)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Transcript:
        session = self.session(language)
        session.audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        return session.finish()

    # ---- one pass ----------------------------------------------------------------------------------
    def decode_batch(self, audios: list, prompts: list[str], prefixes: list[str]) -> list[str]:
        """One pass over one or more utterances: each row's audio and prompt (+ its prefix) → its raw
        text (prefix + what the model wrote). Padded into one batch: the processor pads the prompts on
        the left, so every row's text continues its own prompt."""
        import torch

        p, m = self._processor, self._model
        with torch.inference_mode(), torch.cuda.stream(self._stream):
            inputs = p(text=prompts, audio=list(audios), return_tensors="pt", padding=True).to(m.device).to(m.dtype)
            ids = m.generate(**inputs, max_new_tokens=self.max_new_tokens)
            new = ids.sequences[:, inputs["input_ids"].shape[1]:]
            texts = p.batch_decode(new, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        return [prefix + text for prefix, text in zip(prefixes, texts)]

    def _language(self, language: str | None) -> str | None:
        if not language:
            return None
        from qwen_asr.inference.utils import normalize_language_name, validate_language

        name = normalize_language_name(language)
        validate_language(name)
        return name

    def _prompt(self, language: str | None) -> str:
        """Qwen's prompt; with a language, it asks for that language's text only."""
        return self._asr._build_text_prompt(context="", force_language=self._language(language))
