#!/usr/bin/env python3
"""Точка входа отчёта «аванс по договору».

Примеры:

    python report.py --date 2026-11-10
    python report.py --data ../data --date 2026-11-10 --format all --out out
    python report.py --date 2026-11-10 --format csv --out out

Требования: Python 3.10+ и только стандартная библиотека.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Скрипт можно запускать из любого каталога: пакет лежит рядом с ним.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from advance_report.cli import main  # noqa: E402 — импорт после настройки sys.path

if __name__ == "__main__":
    raise SystemExit(main())
