#!/usr/bin/env python3
"""Chạy tests bằng server/storage tạm, không kết nối database thật của demo."""
from pathlib import Path
import unittest


if __name__ == "__main__":
    tests = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    raise SystemExit(0 if result.wasSuccessful() else 1)
