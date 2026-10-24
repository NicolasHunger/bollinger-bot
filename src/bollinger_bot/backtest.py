"""Backtest: der echte `tick()` auf aufgezeichneten Kursen statt auf Bitget."""

import csv
from datetime import UTC, datetime, timedelta
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
    """Simulierte Börse auf aufgezeichneten Kerzen, erfüllt den Port `Boerse`.

    Die simulierte Zeit ist das Attribut `jetzt`: Die Backtest-Schleife setzt es vor jedem
    tick() auf das Ende der Kerze, die gerade fertig wird. Ein Attribut statt einer Methode, weil
    es nichts weiter zu tun gibt, als die Zeit zu merken.
    """

    def __init__(
        self,
        *,
        kerzen: list[Kerze],
        intervall_dauer: timedelta,
        startguthaben: float = 1000.0,
        gebuehr_prozent: float = 0.1,
    ):
        self._kerzen = kerzen
        self._intervall_dauer = intervall_dauer
        self._konto = {"USDT": startguthaben, "BTC": 0.0}
        # Anfangs steht die Uhr vor allen Kerzen: Noch ist keine abgeschlossen.
        self.jetzt = datetime.min.replace(tzinfo=UTC)

    def hole_kerzen(self, symbol: str, intervall: str, anzahl: int) -> list[Kerze]:
        """Die letzten `anzahl` Kerzen, die um `jetzt` abgeschlossen sind, älteste zuerst.

        `symbol` und `intervall` werden nicht beachtet: Die aufgezeichneten Kerzen gelten für
        genau ein Handelspaar und ein Intervall.
        """
        abgeschlossen = [
            k for k in self._kerzen if k.startzeit + self._intervall_dauer <= self.jetzt
        ]
        return abgeschlossen[-anzahl:]

    def hole_kontostand(self, waehrung: str) -> float:
        """Guthaben im simulierten Konto. Es kennt nur USDT und BTC, alles andere ist 0.0."""
        return self._konto.get(waehrung, 0.0)
