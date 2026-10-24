"""Backtest: der echte `tick()` auf aufgezeichneten Kursen statt auf Bitget."""

import csv
from datetime import datetime, timedelta
from pathlib import Path

from bollinger_bot.port import Kerze


def lies_kerzen(pfad: str | Path) -> list[Kerze]:
    """Liest Kerzen aus einer CSV im Format von `scripts/kerzen_holen.py`, älteste zuerst."""
    with open(pfad, newline="") as datei:
        kerzen = [
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
    return sorted(kerzen, key=lambda kerze: kerze.startzeit)


class AufgezeichneteBoerse:
    def __init__(
        self,
        *,
        kerzen: list[Kerze],
        intervall_dauer: timedelta,
        startguthaben: float = 1000.0,
        gebuehr_prozent: float = 0.1,
    ):
        pass

    def hole_kerzen(self, symbol: str, intervall: str, anzahl: int) -> list[Kerze]:
        return []
