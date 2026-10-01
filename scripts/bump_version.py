#!/usr/bin/env python3
"""Bump the app version in VERSION.json + src/__init__.py.

Every change/update should bump the version code. Run this before committing:

    python scripts/bump_version.py patch          # bug fixes / minor tweaks
    python scripts/bump_version.py minor          # new features
    python scripts/bump_version.py major          # breaking changes

It updates:
  - VERSION.json  (app_version, git_sha, build_id, deployed_at)
  - src/__init__.py (__version__)
The running app reads VERSION.json on startup, so the footer + X-App-Version
header pick up the new value on the next deploy.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "VERSION.json"
INIT_PATH = ROOT / "src" / "__init__.py"


def _load() -> dict:
    return json.loads(VERSION_PATH.read_text(encoding="utf-8"))


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT
        ).decode().strip()
    except Exception:
        return "unknown"


def bump(part: str) -> None:
    data = _load()
    ver = data.get("app_version", "1.0.0")
    major, minor, patch = (int(x) for x in ver.split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        major, minor, patch = major, minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"

    data["app_version"] = new
    data["git_sha"] = _git_sha()
    data["build_id"] = f"{datetime.now(timezone.utc).strftime('%Y%m%d')}-{part}"
    data["deployed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    VERSION_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    init = INIT_PATH.read_text(encoding="utf-8")
    init = re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{new}"', init)
    INIT_PATH.write_text(init, encoding="utf-8")

    print(f"Bumped {ver} -> {new} ({part})")
    print(f"  git_sha:  {data['git_sha']}")
    print(f"  build_id: {data['build_id']}")


if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "patch"
    if part not in ("patch", "minor", "major"):
        print(f"Unknown part '{part}'. Use patch|minor|major.")
        sys.exit(1)
    bump(part)
