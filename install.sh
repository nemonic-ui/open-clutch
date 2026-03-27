#!/bin/bash
# OPENCLUTCH Onboarding — single install script
# Usage: curl -sSf https://raw.githubusercontent.com/nemonic-ui/open-clutch/main/install.sh | sh

set -e

# Ensure this script is executable if run directly
chmod +x "$0" 2>/dev/null || true

ONBOARD_URL="https://raw.githubusercontent.com/nemonic-ui/open-clutch/main/onboard.py"
OLLAMA_INSTALL="https://ollama.com/install.sh"
MODEL="llama3.2:3b"

# 1. Check for Python 3
if ! command -v python3 >/dev/null 2>&1; then
  echo "  [!] Python 3 required. Install it and re-run."
  exit 1
fi

# 2. Check / install Ollama
if ! command -v ollama >/dev/null 2>&1; then
  echo "  Installing Ollama..."
  curl -fsSL "$OLLAMA_INSTALL" | sh
else
  echo "  Ollama: found"
fi

# 3. Start Ollama daemon if not running
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "  Starting Ollama daemon..."
  ollama serve &>/tmp/ollama.log &
  sleep 3
fi

# 4. Pull onboarding model
if ! ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
  echo "  Pulling ${MODEL}... (first run only, ~2GB)"
  ollama pull "$MODEL"
fi

# 5. Download onboard.py
echo "  Fetching onboard.py..."
curl -sSf "$ONBOARD_URL" -o /tmp/openclutch_onboard.py

# 6. Launch
echo ""
python3 /tmp/openclutch_onboard.py
