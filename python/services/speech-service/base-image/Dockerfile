# syntax=docker/dockerfile:1
# speech-base: speech-service's model stack, the big and rarely changing part of its image
# (infra/images/speech-base.yaml): PyTorch 2.15 with its CUDA 13.2 libraries, Triton, transformers
# 4.57.6, Qwen's qwen-tts and qwen-asr, the web stack. ~7 GB; the service image adds its code on top.
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
RUN python -m venv /venv \
 && pip install --no-deps --extra-index-url https://download.pytorch.org/whl/cu132 -r /tmp/requirements.lock \
 && python -c "import torch, triton, transformers, qwen_tts, qwen_asr, fastapi, uvicorn; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'transformers', transformers.__version__)"
