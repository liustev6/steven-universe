#!/bin/bash
# A wake-word training run on the playground, then its evaluation; logs served at :8080.
#   train-run.sh <run name> <config> <evaluate.py>       e.g. train-run.sh xiao_pai_03 config-xiao-pai-03.yaml spikes/03-many-voices/evaluate.py
RUN=$1; CONFIG=$2; EVAL=$3
W=/srv/src/steven-universe/experiments/python/wake-word; E=/srv/experiments/wake-word; L=$E/logs
export WORK=$E/work DATA=$E/data/xiao_pai OUT=$E/out/models
export CUDA_VISIBLE_DEVICES="" TF_CPP_MIN_LOG_LEVEL=1   # CPU only: the GPU is speech-service's; TensorFlow's notices off
cd $W
readable() { stdbuf -oL tr "\r" "\n" | grep --line-buffered -E "==>|positives:|Step [0-9]+ \(nonstreaming\)|best|Cutoff|AUC|Error|Traceback|clips\)" | grep --line-buffered -vE "oneDNN|cuda_executor|cuDNN"; }
echo "$(date +%H:%M:%S) $RUN: training with $CONFIG" | tee $L/$RUN.log
.venv/bin/python -u train.py $CONFIG 2>&1 | tee $L/$RUN.raw.log | readable >> $L/$RUN.log
echo "$(date +%H:%M:%S) $RUN: evaluating $OUT/$RUN.json with $EVAL" >> $L/$RUN.log
/opt/uv/bin/uv pip install -q -p .venv "pymicro-wakeword==2.5.0"
.venv/bin/python -u $EVAL $OUT/$RUN.json $DATA 2>&1 | grep -vE "^INFO|XNNPACK" | tee $L/$RUN.eval.txt >> $L/$RUN.log
echo "$(date +%H:%M:%S) $RUN: done" >> $L/$RUN.log
