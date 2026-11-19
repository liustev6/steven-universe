#!/bin/bash
# A wake-word training run on the playground, then its evaluation; logs served at :8080.
#   train-run.sh <run name> <config> <evaluate.py> [another test]   e.g. train-run.sh xiao_pai_03 config-xiao-pai-03.yaml spikes/03-many-voices/evaluate.py
#   GPU=1 train-run.sh …    on the card (speech-service stopped by hand: docs/playground.md); CPU otherwise
RUN=$1; CONFIG=$2; EVAL=$3; EVAL2=$4
W=/srv/src/steven-universe/experiments/python/wake-word; E=/srv/experiments/wake-word; L=$E/logs
export WORK=$E/work DATA=$E/data/xiao_pai OUT=$E/out/models
export TF_CPP_MIN_LOG_LEVEL=1                          # TensorFlow's notices off
cd $W
if [ "$GPU" = 1 ]; then                                # tensorflow[and-cuda]'s libraries; cuSOLVER isn't found otherwise
  SP=$(.venv/bin/python -c "import site; print(site.getsitepackages()[0])")
  export LD_LIBRARY_PATH=$(ls -d $SP/nvidia/*/lib | paste -sd:) TF_FORCE_GPU_ALLOW_GROWTH=true
else
  export CUDA_VISIBLE_DEVICES=""                       # CPU: the GPU is speech-service's
fi
readable() { stdbuf -oL tr "\r" "\n" | grep --line-buffered -E "==>|positives:|Step [0-9]+ \(nonstreaming\)|best|Cutoff|AUC|Error|Traceback|clips\)" | grep --line-buffered -vE "oneDNN|cuda_executor|cuDNN"; }
echo "$(date +%H:%M:%S) $RUN: training with $CONFIG on the $([ "$GPU" = 1 ] && echo GPU || echo CPU)" | tee $L/$RUN.log
.venv/bin/python -u train.py $CONFIG 2>&1 | tee $L/$RUN.raw.log | readable >> $L/$RUN.log
echo "$(date +%H:%M:%S) $RUN: evaluating $OUT/$RUN.json with $EVAL" >> $L/$RUN.log
/opt/uv/bin/uv pip install -q -p .venv "pymicro-wakeword==2.5.0"
.venv/bin/python -u $EVAL $OUT/$RUN.json $DATA 2>&1 | grep -vE "^INFO|XNNPACK" | tee $L/$RUN.eval.txt >> $L/$RUN.log
[ -n "$EVAL2" ] && .venv/bin/python -u $EVAL2 $OUT/$RUN.json $DATA --held-out 2>&1 | grep -vE "^INFO|XNNPACK" | tee -a $L/$RUN.eval.txt >> $L/$RUN.log
echo "$(date +%H:%M:%S) $RUN: done" >> $L/$RUN.log
