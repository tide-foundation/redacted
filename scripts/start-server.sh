#!/bin/sh
set -eu
# Compose downloads assets before starting the app. Never fetch on first use.
python /app/scripts/download-model.py --check
# All processing after initialization uses the local checkpoint and tokenizer.
export HF_HUB_OFFLINE=1
exec "$@"
