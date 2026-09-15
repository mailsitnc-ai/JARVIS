"""JARVIS 1.0 entry point. Usage: jarvis [command]   (jarvis --help for the list)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
