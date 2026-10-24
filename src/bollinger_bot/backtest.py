"""Backtest: der echte `tick()` auf aufgezeichneten Kursen statt auf Bitget."""

import csv
from datetime import datetime
from pathlib import Path

from bollinger_bot.port import Kerze


def lies_kerzen(pfad: str | Path) -> list[Kerze]:
    """Liest Kerzen aus einer CSV im Format von `scripts/kerzen_holen.py`."""
    with open(pfad, newline="") as datei:
        return [
            Kerze(
                startzeit=datetime.fromisoformat(zeile["startzeit"]),
                eroeffnung=float(zeile["eroeffnung"]),
                hoch=float(zeile["hoch"]),
                tief=float(zeile["tief"]),
                schluss=float(zeile["schluss"]),
                volumen=float(zeile["volumen"]),
            )
            for zeile in csv.DictReader(datei)
        ]
