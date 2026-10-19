#!/bin/bash
# One-time setup (needs internet).  bash setup.sh [langs]   e.g. bash setup.sh en,fr,es,de
set -e; cd "$(dirname "$0")"
PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
echo "Using $PY ($($PY --version))"
$PY -m venv .venv && source .venv/bin/activate
pip install -U pip -q && pip install -r requirements.txt
python server.py --download "${1:-en,fr,es}"
echo; echo "Optional, better translations:  brew install ollama && ollama pull gemma3:4b"
echo "Setup complete. Start with:  bash run.sh"
