"""Does the TTS give the same codes on transformers 4.57.6 (qwen-asr's pin) as on 4.57.3 (qwen-tts's)?

Greedy codes for five sentences, Qwen's own path and ours (fused predictor, graphed talker): the
first run saves them, a run on the other version compares. Both versions are patch releases of 4.57.

    docker run ... tts-spike-official python spikes/10-stt/tts_same.py save /out/tts-codes-4.57.3.pt
    docker run ... speech-spike       python spikes/10-stt/tts_same.py compare /out/tts-codes-4.57.3.pt
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import torch, transformers
from app.tts_engine.engine import QwenTTSEngine

SENTENCES = [("ryan", "Hi, how can I help you today?"), ("vivian", "好的，我现在帮你把客厅的灯关掉。"),
             ("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon."),
             ("serena", "今天西雅图多云，最高气温十二度。"), ("ryan", "The kitchen lights are on, and the door is locked.")]
mode, path = sys.argv[1], sys.argv[2]
e = QwenTTSEngine(os.environ["TTS_MODEL"]); e.load()
codes = {}
for setup in ("qwen:qwen", "ours-fused:ours-graph"):
    e.predictor, e.talker = setup.split(":")
    for voice, text in SENTENCES:
        codes[(setup, text)] = torch.stack(list(e.frames(text, voice, sample=False))).cpu()
print("transformers", transformers.__version__)
if mode == "save":
    torch.save(codes, path); print("saved", len(codes))
else:
    ref = torch.load(path)
    for key, got in codes.items():
        want = ref[key]
        same = want.shape == got.shape and torch.equal(want, got)
        print(f"{'same' if same else 'DIFFERENT'}  {key[0]:<22} {tuple(want.shape)} vs {tuple(got.shape)}  {key[1][:30]}")
