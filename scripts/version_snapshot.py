#!/usr/bin/env python3
"""Create a version snapshot manifest for local repo and optional NAS mirror."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACK_FILES = [
    "api.py",
    "VERSION.json",
    "static/templates/index.html",
    "templates/index.html",
    "src/main.py",
    "main.py",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest() -> dict:
    version = {}
    vpath = ROOT / "VERSION.json"
    if vpath.exists():
        version = json.loads(vpath.read_text(encoding="utf-8"))

    files = {}
    for rel in TRACK_FILES:
        p = ROOT / rel
        if p.exists():
            files[rel] = {
                "sha256": sha256_file(p),
                "bytes": p.stat().st_size,
            }

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "project": "agentdebate-backend",
        "version": version,
        "files": files,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nas-dir", default="", help="Optional NAS output dir")
    args = parser.parse_args()

    manifest = build_manifest()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "version-history"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"manifest-{stamp}.json"
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"LOCAL_MANIFEST={out_path}")

    if args.nas_dir:
        nas_dir = Path(args.nas_dir).expanduser()
        nas_dir.mkdir(parents=True, exist_ok=True)
        nas_path = nas_dir / out_path.name
        nas_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"NAS_MANIFEST={nas_path}")


if __name__ == "__main__":
    main()
