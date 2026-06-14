#!/usr/bin/env bash
# Download Oracle's prebuilt augmented all-MiniLM-L12-v2 ONNX model into oracle/models/.
# Resolves Oracle's CURRENT download link at runtime (the pre-authenticated URLs rotate,
# so we follow the stable docs redirect instead of hard-coding a link).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p models

if [ -f models/all_MiniLM_L12_v2.onnx ]; then
  echo "model already present: models/all_MiniLM_L12_v2.onnx"
  exit 0
fi

echo "resolving current Oracle model index..."
INDEX=$(curl -fsSL -o /dev/null -w '%{url_effective}' \
  "https://docs.oracle.com/pls/topic/lookup?ctx=en/database/oracle/oracle-database/26/vecse&id=oml_ai_models_object_storage")

echo "finding the MiniLM model link..."
ZIP=$(curl -fsSL "$INDEX" | grep -oE 'https://[^"]*all_MiniLM_L12_v2_augmented\.zip' | head -1)
if [ -z "${ZIP:-}" ]; then
  echo "Could not resolve the model URL automatically."
  echo "Download the augmented all_MiniLM_L12_v2 ONNX model from Oracle's docs and place"
  echo "the .onnx file at oracle/models/all_MiniLM_L12_v2.onnx — see setup/01_load_onnx_model.sql"
  exit 1
fi

echo "downloading model (~120MB)..."
curl -fsSL "$ZIP" -o models/all_MiniLM_L12_v2_augmented.zip
unzip -o models/all_MiniLM_L12_v2_augmented.zip -d models >/dev/null
echo "done: models/all_MiniLM_L12_v2.onnx"
