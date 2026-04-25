"""Download the embedding model for Docker builds.

Downloads model files to docker/models/ in the project directory.
Run this on the host before building the Docker image:

    python docker/download_model.py
    HF_TOKEN=hf_... python docker/download_model.py

The Dockerfile COPYs docker/models/ into the image so no network
access is needed during the build.
"""

import os
from pathlib import Path

from huggingface_hub import snapshot_download

MODEL = "nomic-ai/nomic-embed-text-v1.5"
DEST = Path(__file__).parent / "models"

token = os.environ.get("HF_TOKEN") or None

print(f"Downloading {MODEL} -> {DEST}")
if token:
    print("  (using HF_TOKEN for authenticated download)")
else:
    print("  (no HF_TOKEN set — download may be slow)")

snapshot_download(
    MODEL,
    token=token,
    local_dir=str(DEST),
    ignore_patterns=["onnx/*", "*.onnx"],
)
print("Done.")
