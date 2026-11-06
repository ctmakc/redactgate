"""PyInstaller entry point for RedactGate Desktop."""
import multiprocessing
import sys

from app.desktop.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
