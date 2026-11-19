"""Train the wake word (voice-path.md 4.12b): microWakeWord's training notebook as one script, run as a job
in our own container (Dockerfile here), on our own hardware. Each step is skipped when its output exists, so
a rerun resumes, and the downloads (~6 GB) are fetched once into the cache.

    python train.py config.yaml        (in the trainer image: /work the cache, /data our clips, /out the model)

A config with `run: NAME` keeps that run's positives, features and trained model in /work/runs/NAME (the
downloads stay shared), so a second wake word doesn't overwrite the first; `piper_samples: 0` trains on our
own clips only (Piper's voices are English: a Chinese word has none). `extra_negatives` adds negative sets of
our own (folders of clips: Mandarin conversation, household noise), each with its sampling weight, and
`augmentation.background_dirs` adds background sound mixed into every clip.

Steps, as microWakeWord's notebook (kahrendt/microWakeWord, notebooks/basic_training_notebook.ipynb):
  1. Piper samples of the word (Piper's ONNX LibriTTS voice through piper-tts: 904 speakers)
  2. augmentation audio: MIT room impulse responses, an AudioSet part, the Free Music Archive (xs)
  3. the positives: Piper's and ours (/data/positives/*; your own, `me`, weighted by copies), augmented
     into features (training, validation, testing)
  4. our near misses (/data/negatives/*) the same way, as a negative feature set
  5. microWakeWord's negative features (speech, dinner party, no speech)
  6. training (model_train_eval: MixedNet), converted to a quantized streaming model
  7. the model and its manifest (the fields pymicro-wakeword reads) into /out
The training data's licenses are mixed: the model is for non-commercial personal use.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import yaml

CONFIG = yaml.safe_load(open(sys.argv[1] if len(sys.argv) > 1 else "config.yaml"))
WORK, DATA, OUT = Path(os.environ.get("WORK", "/work")), Path(os.environ.get("DATA", "/data")), Path(os.environ.get("OUT", "/out"))
RUN = WORK / "runs" / CONFIG["run"] if CONFIG.get("run") else WORK       # this run's own files
T0 = time.monotonic()


def step(name: str) -> None:
    print(f"\n==> [{(time.monotonic() - T0) / 60:5.1f} min] {name}", flush=True)


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def fetch(url: str, path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # curl (on macOS and Linux); resumes a part already fetched; HTTP/1.1: big files dropped mid-stream on HTTP/2
        run("curl", "-sSLf", "--http1.1", "--retry", "5", "-C", "-", "-o", str(path) + ".part", url)
        os.replace(str(path) + ".part", path)


# ---- 1. Piper samples --------------------------------------------------------------------------------------
PIPER_PHRASES = ["hey pie", "hey pi", "Hey Pie!", "Hey, Pi.", "hey pie?"]


def piper_samples() -> Path | None:
    """The word said by Piper's LibriTTS voice (its ONNX model: 904 speakers; weights only, nothing in the file
    runs), each sample a random speaker, speed and variation, as piper-sample-generator varies them; 16 kHz, the
    silence trimmed to 150 ms at each end."""
    import random

    import librosa
    import numpy as np
    import scipy.io.wavfile
    from piper import PiperVoice, SynthesisConfig

    if not CONFIG.get("piper_samples"):
        return None
    out = WORK / "piper" / CONFIG["word"].replace(" ", "_")
    have = len(list(out.glob("*.wav")))
    if have >= CONFIG["piper_samples"]:
        return out
    step(f"1. {CONFIG['piper_samples']} Piper samples (the ONNX LibriTTS voice, 904 speakers)")
    out.mkdir(parents=True, exist_ok=True)
    voice = PiperVoice.load(str(WORK / "piper-onnx/en_US-libritts_r-medium.onnx"))
    speakers = voice.config.num_speakers
    rng = random.Random(10)
    phrases = CONFIG.get("piper_phrases", PIPER_PHRASES)
    for i in range(CONFIG["piper_samples"]):
        cfg = SynthesisConfig(speaker_id=rng.randrange(speakers), length_scale=rng.uniform(0.75, 1.25),
                              noise_scale=rng.uniform(0.5, 1.0), noise_w_scale=rng.uniform(0.6, 1.0))
        if i < have:
            continue                                         # resume: the same draws, the same files
        chunks = list(voice.synthesize(rng.choice(phrases), cfg))
        x = np.concatenate([c.audio_float_array for c in chunks])
        y = librosa.resample(x, orig_sr=chunks[0].sample_rate, target_sr=16000)
        loud = np.flatnonzero(np.abs(y) > 10 ** (-45 / 20))
        if len(loud):
            y = y[max(0, loud[0] - 2400): loud[-1] + 2400]
        scipy.io.wavfile.write(out / f"{i:05d}.wav", 16000, (np.clip(y, -1, 1) * 32767).astype(np.int16))
    return out


# ---- 2. augmentation audio -----------------------------------------------------------------------------------
def augmentation_audio() -> None:
    import datasets
    import numpy as np
    import scipy.io.wavfile
    from tqdm import tqdm

    rirs = WORK / "mit_rirs"
    if not rirs.exists():
        step("2a. MIT room impulse responses")
        rirs.mkdir(parents=True)
        for row in tqdm(datasets.load_dataset("davidscripka/MIT_environmental_impulse_responses", split="train", streaming=True)):
            name = row["audio"]["path"].split("/")[-1]
            scipy.io.wavfile.write(rirs / name, 16000, (row["audio"]["array"] * 32767).astype(np.int16))
    audioset = WORK / "audioset_16k"
    if not audioset.exists():
        # AudioSet on Hugging Face is now Parquet shards (the notebook's .tar links are gone): one shard of the
        # balanced set, as the notebook took one part, its audio decoded and resampled to 16 kHz.
        step("2b. AudioSet (one shard of the balanced set)")
        shard = WORK / "audioset/bal_train-09.parquet"
        fetch("https://huggingface.co/datasets/agkphysics/AudioSet/resolve/main/data/bal_train/09.parquet", shard)
        # read with pyarrow (the shard was written by a newer `datasets` than ours): each row's FLAC bytes,
        # decoded, mixed to mono, resampled to 16 kHz
        import io

        import librosa
        import pyarrow.parquet as pq
        import soundfile

        audioset.mkdir(parents=True)
        table = pq.read_table(shard, columns=["audio"])
        for i, cell in enumerate(tqdm(table.column("audio").to_pylist())):
            y, sr = soundfile.read(io.BytesIO(cell["bytes"]), dtype="float32", always_2d=True)
            y = librosa.resample(y.mean(axis=1), orig_sr=sr, target_sr=16000)
            scipy.io.wavfile.write(audioset / f"audioset_{i:05d}.wav", 16000, (np.clip(y, -1, 1) * 32767).astype(np.int16))
    fma = WORK / "fma_16k"
    if not fma.exists():
        step("2c. Free Music Archive (xs)")
        archive = WORK / "fma/fma_xs.zip"
        fetch("https://huggingface.co/datasets/mchl914/fma_xsmall/resolve/main/fma_xs.zip", archive)
        run("unzip", "-q", "-o", str(archive), "-d", str(archive.parent))
        files = [str(p) for p in archive.parent.glob("**/*.mp3")]
        ds = datasets.Dataset.from_dict({"audio": files}).cast_column("audio", datasets.Audio(sampling_rate=16000))
        fma.mkdir(parents=True)
        for row in tqdm(ds):
            scipy.io.wavfile.write(fma / (Path(row["audio"]["path"]).stem + ".wav"), 16000,
                                   (row["audio"]["array"] * 32767).astype(np.int16))


# ---- 3-4. features ------------------------------------------------------------------------------------------
def augmenter():
    from microwakeword.audio.augmentation import Augmentation

    a = CONFIG["augmentation"]
    backgrounds = [str(WORK / "fma_16k"), str(WORK / "audioset_16k")] + [str(Path(d)) for d in a.get("background_dirs", [])]
    return Augmentation(augmentation_duration_s=3.2, augmentation_probabilities=a["probabilities"],
                        impulse_paths=[str(WORK / "mit_rirs")],
                        background_paths=backgrounds,
                        background_min_snr_db=a["background_min_snr_db"], background_max_snr_db=a["background_max_snr_db"],
                        min_jitter_s=0.195, max_jitter_s=0.205)


def features(name: str, clips_dir: Path, aug, repeat: int = 2) -> Path:
    """Augmented spectrograms of a clip folder, split into training (sliding 10 frames, `repeat` times over:
    twice, as the notebook; once for a big negative set), validation, testing (as the streaming model sees them)."""
    from microwakeword.audio.clips import Clips
    from microwakeword.audio.spectrograms import SpectrogramGeneration
    from mmap_ninja.ragged import RaggedMmap

    import hashlib

    out = RUN / "features" / name
    names = sorted(p.name for p in clips_dir.glob("*.wav"))
    fingerprint = hashlib.sha256("\n".join(names).encode()).hexdigest()
    if (out / "testing").exists() and (out / ".clips").exists() and (out / ".clips").read_text() == fingerprint:
        return out                                            # made from these same clips already
    shutil.rmtree(out, ignore_errors=True)
    step(f"features: {name} ({len(list(clips_dir.glob('*.wav')))} clips)")
    clips = Clips(input_directory=str(clips_dir), file_pattern="*.wav", max_clip_duration_s=None,
                  remove_silence=False, random_split_seed=10, split_count=0.1)
    for split, split_name, repeat, slide in (("training", "train", repeat, 10), ("validation", "validation", 1, 10),
                                             ("testing", "test", 1, 1)):
        d = out / split
        d.mkdir(parents=True, exist_ok=True)
        spectrograms = SpectrogramGeneration(clips=clips, augmenter=aug, slide_frames=slide, step_ms=10)
        RaggedMmap.from_generator(out_dir=str(d / f"{name}_mmap"),
                                  sample_generator=spectrograms.spectrogram_generator(split=split_name, repeat=repeat),
                                  batch_size=100, verbose=True)
    (out / ".clips").write_text(fingerprint)
    return out


def gather(target: Path, sources: list[tuple[Path, int]]) -> Path:
    """Clip folders into one, each `copies` times (a weight)."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for folder, copies in sources:
        for wav in folder.glob("*.wav"):
            for k in range(copies):
                shutil.copy(wav, target / f"{folder.name}_{k}_{wav.name}")
    return target


# ---- 5. microWakeWord's negatives -------------------------------------------------------------------------------
def negative_datasets() -> Path:
    out = WORK / "negative_datasets"
    for name in ("dinner_party", "dinner_party_eval", "no_speech", "speech"):
        if (out / name).exists():
            continue
        step(f"5. negatives: {name}")
        archive = out / f"{name}.zip"
        fetch(f"https://huggingface.co/datasets/kahrendt/microwakeword/resolve/main/{name}.zip", archive)
        zipfile.ZipFile(archive).extractall(out)
        archive.unlink()
    return out


# ---- 6-7. training, the model ------------------------------------------------------------------------------------
def train(positive: Path, near: Path | None, negatives: Path, extra: list[tuple[Path, float]] = ()) -> None:
    t = CONFIG["training"]
    feats = [{"features_dir": str(positive), "sampling_weight": 2.0, "penalty_weight": 1.0, "truth": True,
              "truncation_strategy": "truncate_start", "type": "mmap"}]
    if near is not None:
        feats.append({"features_dir": str(near), "sampling_weight": t["near_miss_weight"], "penalty_weight": 1.0,
                      "truth": False, "truncation_strategy": "random", "type": "mmap"})
    for name, weight in (("speech", 10.0), ("dinner_party", 10.0), ("no_speech", 5.0)):
        feats.append({"features_dir": str(negatives / name), "sampling_weight": weight, "penalty_weight": 1.0,
                      "truth": False, "truncation_strategy": "random", "type": "mmap"})
    for features_dir, weight in extra:                       # our own negative sets (extra_negatives)
        feats.append({"features_dir": str(features_dir), "sampling_weight": weight, "penalty_weight": 1.0,
                      "truth": False, "truncation_strategy": "random", "type": "mmap"})
    feats.append({"features_dir": str(negatives / "dinner_party_eval"), "sampling_weight": 0.0, "penalty_weight": 1.0,
                  "truth": False, "truncation_strategy": "split", "type": "mmap"})
    params = {
        "window_step_ms": 10, "train_dir": str(RUN / "trained"), "features": feats,
        "training_steps": [t["steps"]], "positive_class_weight": [1], "negative_class_weight": [t["negative_class_weight"]],
        "learning_rates": [0.001], "batch_size": 128,
        "time_mask_max_size": [0], "time_mask_count": [0], "freq_mask_max_size": [0], "freq_mask_count": [0],
        "eval_step_interval": 500, "clip_duration_ms": 1500,
        "target_minimization": 0.9, "minimization_metric": None, "maximization_metric": "average_viable_recall",
    }
    with open(RUN / "training_parameters.yaml", "w") as f:
        yaml.dump(params, f)
    step(f"6. training: {t['steps']} steps")
    run(sys.executable, "-m", "microwakeword.model_train_eval", f"--training_config={RUN / 'training_parameters.yaml'}",
        "--train", "1", "--restore_checkpoint", "1", "--test_tf_nonstreaming", "0", "--test_tflite_nonstreaming", "0",
        "--test_tflite_nonstreaming_quantized", "0", "--test_tflite_streaming", "0",
        "--test_tflite_streaming_quantized", "1", "--use_weights", "best_weights",
        "mixednet", "--pointwise_filters", "64,64,64,64", "--repeat_in_block", "1, 1, 1, 1",
        "--mixconv_kernel_sizes", "[5], [7,11], [9,15], [23]", "--residual_connection", "0,0,0,0",
        "--first_conv_filters", "32", "--first_conv_kernel_size", "5", "--stride", "3")


def model() -> None:
    step("7. the model and its manifest")
    name = CONFIG["model_name"]
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(RUN / "trained/tflite_stream_state_internal_quant/stream_state_internal_quant.tflite", OUT / f"{name}.tflite")
    manifest = {"type": "micro", "wake_word": CONFIG["wake_word"], "author": "steven-universe (experiments/python/wake-word)",
                "website": "https://github.com/kahrendt/microWakeWord", "model": f"{name}.tflite",
                "trained_languages": CONFIG.get("languages", ["en"]), "version": 2,
                "micro": {"probability_cutoff": CONFIG["probability_cutoff"], "feature_step_size": 10,
                          "sliding_window_size": 5, "tensor_arena_size": 30000, "minimum_esphome_version": "2024.7.0"}}
    (OUT / f"{name}.json").write_text(json.dumps(manifest, indent=2))
    print(f"   {OUT / name}.tflite, {OUT / name}.json", flush=True)


if __name__ == "__main__":
    piper = piper_samples()
    augmentation_audio()
    aug = augmenter()
    ours = sorted(p for p in (DATA / "positives").iterdir() if p.is_dir())
    RUN.mkdir(parents=True, exist_ok=True)
    positives = gather(RUN / "positives", ([(piper, 1)] if piper else [])
                       + [(p, CONFIG["own_voice_copies"] if p.name == "me" else 1) for p in ours])
    print(f"   positives: {len(list(positives.glob('*.wav')))} clips ({', '.join(p.name for p in ours)}"
          f"{' + Piper' if piper else ''})")
    positive = features("positive", positives, aug)
    near = None
    if (DATA / "negatives").exists():
        near_dir = gather(RUN / "near_misses", [(p, 1) for p in sorted((DATA / "negatives").iterdir()) if p.is_dir()])
        near = features("near_miss", near_dir, aug)
    extra = [(features(n["name"], Path(n["dir"]), aug, repeat=n.get("repeat", 1)), float(n["weight"]))
             for n in CONFIG.get("extra_negatives", [])]
    negatives = negative_datasets()
    train(positive, near, negatives, extra)
    model()
    step("done")
