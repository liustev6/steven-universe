"""D: faster-qwen3-tts (CUDA graphs), streaming: the reference for what graphs buy on this card."""
import numpy as np, soundfile as sf, torch
from faster_qwen3_tts import FasterQwen3TTS
from common import MODEL, SENTENCES, Clock, report

torch.manual_seed(0)
c = Clock()
m = FasterQwen3TTS.from_pretrained(MODEL, device="cuda", dtype=torch.bfloat16, local_files_only=True)
print(f"loaded (graphs captured) in {c.ms():.0f} ms; VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GiB", flush=True)

def stream(text, lang, spk, chunk):
    c, first, parts, sr = Clock(), None, [], 24000
    for audio, sr, _info in m.generate_custom_voice_streaming(text=text, speaker=spk, language=lang, chunk_size=chunk):
        if first is None:
            first = c.ms()
        parts.append(np.asarray(audio).reshape(-1))
    torch.cuda.synchronize()
    return np.concatenate(parts), sr, first, c.ms()

for chunk in (4, 12):
    stream("Warm up, please.", "English", "Ryan", chunk)
    rows = []
    for i, (lang, spk, text) in enumerate(SENTENCES):
        wav, sr, first, total = stream(text, lang, spk, chunk)
        sf.write(f"/out/D{chunk}_{i}.wav", wav, sr)
        rows.append({"text": text[:20], "audio_s": len(wav) / sr, "total_ms": total, "first_ms": first})
    report(f"D faster-qwen3-tts, CUDA graphs, streaming, chunk {chunk} frames", rows)
print(f"peak VRAM {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB", flush=True)
