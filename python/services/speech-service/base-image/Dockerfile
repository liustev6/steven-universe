# syntax=docker/dockerfile:1
# speech-base: speech-service's model stack, the big and rarely changing part of its image
# (infra/images/speech-base.yaml): PyTorch 2.15 with its CUDA 13.2 libraries, Triton, transformers
# 4.57.6, Qwen's qwen-tts and qwen-asr, wetext (text normalization), the web stack. ~7 GB; the service image adds its code on top.
#   docker build python/services/speech-service/base-image
#
# No CUDA toolkit: PyTorch's wheels carry the CUDA libraries they need (nvidia-* packages), and the
# GPU's driver comes from the host (the NVIDIA container toolkit). Spec: docs/specs/speech-service.md.

FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PATH=/venv/bin:$PATH
# gcc + libc headers: Triton compiles a small C launcher the first time each kernel runs.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
# wetext (text normalization before the TTS, spike 13) ships grammars for 20 languages (294 MB): keep
# the ones speech-service's voices speak (Chinese, English, Japanese, Korean) and the shared processors;
# other languages pass through unnormalized (app/work/normalize.py).
RUN python -m venv /venv \
 && pip install --no-deps --extra-index-url https://download.pytorch.org/whl/cu132 -r /tmp/requirements.lock \
 && find /venv/lib/python3.12/site-packages/wetext/fsts -mindepth 1 -maxdepth 1 -type d \
      ! -name zh ! -name en ! -name ja ! -name ko ! -name processors -exec rm -rf {} + \
 && python -c "import torch, triton, transformers, qwen_tts, qwen_asr, fastapi, uvicorn; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'transformers', transformers.__version__)" \
 && python -c "from wetext import Normalizer; print(Normalizer(lang='zh').normalize('2026年'), '|', Normalizer(lang='en').normalize('5,959 m'))"
