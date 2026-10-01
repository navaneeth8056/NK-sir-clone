"""Vercel entry point: Vercel runs this file as a Python function and serves the FastAPI app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.server import app  # noqa: E402,F401
