"""Build-time: vendor the default cross-encoder reranker into the image.

Downloads the model files with plain HTTP GET instead of ``huggingface_hub``,
because some networks block the HEAD requests the Hub client uses to fetch cache
metadata — which makes every hub download fail even though the files are reachable
by GET. The reranker adapter loads this baked copy from disk at runtime
(``api/adapters/reranker/cross_encoder.py``), so no HuggingFace access is needed
after build and the image works under ``HF_HUB_OFFLINE=1``.
"""
from __future__ import annotations

import os
import sys

import requests

REPO = "cross-encoder/ms-marco-MiniLM-L6-v2"
DEST = os.path.join(os.environ.get("EMBEDBASE_MODELS_DIR", "/opt/models"), REPO.split("/")[-1])
BASE = f"https://huggingface.co/{REPO}/resolve/main/"
# tokenizer.json is optional (fast tokenizer); vocab.txt suffices for the slow one.
OPTIONAL = {"special_tokens_map.json", "tokenizer.json"}
CONFIG_FILES = ["config.json", "tokenizer_config.json", "vocab.txt", "special_tokens_map.json", "tokenizer.json"]

os.makedirs(DEST, exist_ok=True)


def fetch(name: str) -> bool:
    resp = requests.get(BASE + name, timeout=180)
    if resp.status_code == 200:
        with open(os.path.join(DEST, name), "wb") as fh:
            fh.write(resp.content)
        print(f"  {name}: {len(resp.content)} bytes")
        return True
    if name not in OPTIONAL:
        sys.exit(f"FATAL: required file {name} -> HTTP {resp.status_code}")
    print(f"  {name}: skipped (HTTP {resp.status_code})")
    return False


for fname in CONFIG_FILES:
    fetch(fname)
if not fetch("model.safetensors") and not fetch("pytorch_model.bin"):
    sys.exit("FATAL: no weights file (model.safetensors / pytorch_model.bin) found")

print(f"vendored {REPO} -> {DEST}")
