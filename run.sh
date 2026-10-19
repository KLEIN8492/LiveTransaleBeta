#!/bin/bash
cd "$(dirname "$0")"; source .venv/bin/activate
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec python server.py "$@"
