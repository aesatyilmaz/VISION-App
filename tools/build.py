"""Build VISION App into a double-clickable Windows app.

    pip install pyinstaller
    python tools/build.py

Writes `dist/VISION App/`, which contains `VISION App.exe` and everything it needs.
Zip that folder and anyone on Windows can unzip it and run it — no Python, no pip.

One-folder, not one-file, on purpose. A one-file build unpacks its whole payload into
a temp directory on every launch, which for a bundle this size is a visible wait each
time, and single-file PyInstaller executables are also what antivirus heuristics flag
most often.

**MediaPipe's models are not Python, so PyInstaller does not find them.** The graph
files are loaded at runtime by path (`mediapipe/modules/...`), so they have to be
copied in explicitly and land at exactly that relative path — without this the app
builds, starts, opens the camera, and dies the moment a hand appears. Only the hand
and palm models are copied; the face, pose and iris models in the same folder are
another 20 MB of things this app never asks for.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "VISION App"

_MP_MODULES = ("hand_landmark", "palm_detection")


def mediapipe_data() -> list[str]:
    import mediapipe
    mp_root = Path(mediapipe.__file__).parent
    out = []
    for mod in _MP_MODULES:
        src = mp_root / "modules" / mod
        if not src.is_dir():
            sys.exit(f"mediapipe model folder missing: {src}")
        out += ["--add-data", f"{src}{os.pathsep}mediapipe/modules/{mod}"]
    return out


def main() -> None:
    for stale in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(stale, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir",
        "--name", NAME,
        # Nothing here uses these; without the excludes PyInstaller follows optional
        # imports inside mediapipe and drags in gigabytes.
        "--exclude-module", "torch",
        "--exclude-module", "PySide6",
        "--exclude-module", "pytest",
        # matplotlib is NOT excluded, however much it looks unused. `mediapipe.solutions`
        # imports drawing_utils in its package __init__, so merely reaching for
        # mp.solutions.hands pulls matplotlib in. Excluding it builds fine and then
        # fails on launch with ModuleNotFoundError — which is what --selftest is for.
        *mediapipe_data(),
        str(ROOT / "vision.py"),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)

    out = ROOT / "dist" / NAME
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n{out}  —  {size / 1024 / 1024:.0f} MB")
    print("Zip that folder and put it on the Releases page.")


if __name__ == "__main__":
    main()
