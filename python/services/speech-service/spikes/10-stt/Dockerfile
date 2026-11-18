# Spike only (speech-service 4.5d): the TTS spike image + Qwen3-ASR. qwen-tts pins transformers==4.57.3
# and qwen-asr pins ==4.57.6 (both patch releases of 4.57): 4.57.6, qwen-asr without its pins, and only
# the packages it imports (not gradio/flask: demos; nagisa: imported at load by its forced aligner).
FROM tts-spike-official
ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN python -c "import torch; print(f\"torch=={torch.__version__}\")" > /tmp/keep-torch.txt \
 && pip install --no-cache-dir -c /tmp/keep-torch.txt "transformers==4.57.6" \
 && pip install --no-cache-dir --no-deps "qwen-asr==0.0.6" \
 && pip install --no-cache-dir -c /tmp/keep-torch.txt nagisa==0.2.11 soynlp==0.0.493 qwen-omni-utils \
        jiwer opencc-python-reimplemented pandas pyarrow pytest \
 && python -c "import torch, transformers, qwen_tts, qwen_asr; print(\"torch\", torch.__version__, \"transformers\", transformers.__version__, \"ok\")"
