#!/usr/bin/env bash
# Train the fast spotter and test the two-layer system (spotter + Whisper) on held-out real recordings.
# Expects data/ as built by the steps in README.md. Usage: bash run_spotter.sh <name> [width]
set -euo pipefail
cd "$(dirname "$0")"
PY="../../venv/Scripts/python.exe"
NAME="$1"
quiet() { grep --line-buffered -v "Warning\|warn("; }
"$PY" train_spotter.py data/spot_ds.pkl "data/$NAME.npz" "${2:-64}" 2>&1 | quiet > "data/$NAME.train.log"
"$PY" spot_replay.py data/real/real_all.clips.json data/real/clips_all data/real/negatives.json data/real/negatives \
  data/real/whisper_cache "data/$NAME.npz" 2>&1 | quiet > "data/$NAME.report.txt"
