"""A: the official package, whole sentences. B: our own streaming loop on the official model (plain
PyTorch). C: a batch of 2 vs one at a time. WAVs go to /out for listening."""
import queue, sys, threading
import numpy as np, soundfile as sf, torch
from qwen_tts import Qwen3TTSModel
from common import MODEL, SENTENCES, SR_FRAME, Clock, report

torch.manual_seed(0)
c = Clock()
tts = Qwen3TTSModel.from_pretrained(MODEL, device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa")
model, talker, codec = tts.model, tts.model.talker, tts.model.speech_tokenizer
EOS = model.config.talker_config.codec_eos_token_id
print(f"loaded in {c.ms():.0f} ms; VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GiB", flush=True)

def whole(text, lang, spk):
    wavs, sr = tts.generate_custom_voice(text=text, language=lang, speaker=spk)
    torch.cuda.synchronize()
    return wavs, sr

c = Clock(); whole("Warm up, please.", "English", "Ryan"); print(f"warm-up {c.ms():.0f} ms", flush=True)

# ---- A: the official call: nothing until the whole sentence is made ---------------------------------
rows = []
for i, (lang, spk, text) in enumerate(SENTENCES):
    c = Clock(); wavs, sr = whole(text, lang, spk); t = c.ms()
    sf.write(f"/out/A{i}.wav", wavs[0], sr)
    rows.append({"text": text[:20], "audio_s": len(wavs[0]) / sr, "total_ms": t, "first_ms": t})
report("A official, whole sentence (first packet = the whole sentence)", rows)

# ---- B: our streaming loop: official generate in a thread, codes caught per frame, chunked decode ----
def stream(text, lang, spk, chunk=4, context=25):
    frames, got = [], queue.Queue()
    def hook(module, args, out):                      # each talker step: the full codes of one frame
        codes = out.hidden_states[1]
        if codes is not None:
            got.put(codes[0].detach().clone())
    handle = talker.register_forward_hook(hook)
    input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
    def run():
        try:
            model.generate(input_ids=input_ids, instruct_ids=[None], languages=[lang], speakers=[spk],
                           non_streaming_mode=True, **tts._merge_generate_kwargs())
        finally:
            got.put(None)
    c, first, emitted, audio = Clock(), None, 0, []
    worker = threading.Thread(target=run); worker.start()
    def emit():
        nonlocal emitted, first
        start = max(0, emitted - context)
        wav = codec.decode([{"audio_codes": torch.stack(frames[start:])}])[0][0]
        audio.append(wav[(emitted - start) * SR_FRAME:])
        emitted = len(frames)
        if first is None:
            first = c.ms()
    while (item := got.get()) is not None:
        if int(item[0]) == EOS:
            continue
        frames.append(item)
        if len(frames) - emitted >= chunk:
            emit()
    if len(frames) > emitted:
        emit()
    worker.join(); handle.remove(); torch.cuda.synchronize()
    return np.concatenate(audio), first, c.ms()

for chunk in (4, 12):
    stream("Warm up, please.", "English", "Ryan", chunk=chunk)
    rows = []
    for i, (lang, spk, text) in enumerate(SENTENCES):
        wav, first, total = stream(text, lang, spk, chunk=chunk)
        sf.write(f"/out/B{chunk}_{i}.wav", wav, 24000)
        rows.append({"text": text[:20], "audio_s": len(wav) / 24000, "total_ms": total, "first_ms": first})
    report(f"B ours, streaming, plain PyTorch, chunk {chunk} frames ({chunk / 12.5:.2f} s of audio)", rows)

# ---- C: a batch of 2 vs the same two one at a time --------------------------------------------------
rows = []
for a, b in ((0, 1), (2, 3), (4, 5)):
    (la, sa, ta), (lb, sb, tb) = SENTENCES[a], SENTENCES[b]
    c = Clock(); w1, sr = whole(ta, la, sa); w2, _ = whole(tb, lb, sb); one_by_one = c.ms()
    c = Clock()
    wavs, sr = tts.generate_custom_voice(text=[ta, tb], language=[la, lb], speaker=[sa, sb]); torch.cuda.synchronize()
    batched = c.ms()
    audio = (len(wavs[0]) + len(wavs[1])) / sr
    rows.append({"pair": f"{a}+{b}", "audio_s": audio, "total_ms": batched, "one_by_one_ms": one_by_one,
                 "speedup": one_by_one / batched})
report("C a batch of 2 (total_ms) vs one at a time (one_by_one_ms)", rows)
print(f"peak VRAM {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB", flush=True)
