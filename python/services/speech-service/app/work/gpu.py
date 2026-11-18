"""The workers' GPU plumbing, behind a small interface so their tests run without one (NoGPU).

    generation()   context: the TTS's generation stream (normal priority)
    decoding()     context: the TTS's audio decode stream (high priority: playback's hard deadline)
    event()        a CUDA event recorded now, on the current stream (a frame's codes are ready)
    wait(event)    the current stream waits for it, on the GPU (no CPU wait)
    inference()    context: torch.inference_mode
"""

from __future__ import annotations

import contextlib


class CudaGPU:
    def __init__(self, generation_stream, decoding_stream):
        self._generation, self._decoding = generation_stream, decoding_stream

    def generation(self):
        import torch
        return torch.cuda.stream(self._generation)

    def decoding(self):
        import torch
        return torch.cuda.stream(self._decoding)

    def event(self):
        import torch
        e = torch.cuda.Event()
        e.record()
        return e

    def wait(self, event) -> None:
        import torch
        torch.cuda.current_stream().wait_event(event)

    def inference(self):
        import torch
        return torch.inference_mode()


class NoGPU:
    def generation(self):
        return contextlib.nullcontext()

    decoding = inference = generation

    def event(self):
        return None

    def wait(self, event) -> None:
        pass
