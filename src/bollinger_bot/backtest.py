"""Backtest: der echte `tick()` auf aufgezeichneten Kursen statt auf Bitget."""

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bollinger_bot.port import Kerze, Order
from bollinger_bot.risiko import BTCUSDT_SPOT, abrunden
from bollinger_bot.speicher import Speicher
from bollinger_bot.tick import Einstellungen, tick


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
        self._gebuehr_prozent = gebuehr_prozent
        self._konto = {"USDT": startguthaben, "BTC": 0.0}
        self._orders = 0
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

    def kaufe(self, symbol: str, betrag: float) -> Order:
        """Kauft für `betrag` USDT zum Schlusskurs der zuletzt abgeschlossenen Kerze.

        Wie bei Bitget geht die Gebühr von den gekauften BTC ab, `Order.menge` ist schon ohne
        Gebühr.
        """
        if betrag > self._konto["USDT"]:
            raise ValueError(
                f"Kauf für {betrag} USDT abgelehnt: frei sind nur {self._konto['USDT']} USDT"
            )
        kurs = self.hole_kerzen(symbol, "", 1)[-1].schluss
        # Bitget rundet die gekaufte Menge ab und gibt nur aus, was diese Menge kostet.
        gekauft = abrunden(betrag / kurs, BTCUSDT_SPOT.mengen_stellen)
        gebuehr = gekauft * self._gebuehr_prozent / 100
        self._konto["USDT"] -= gekauft * kurs
        self._konto["BTC"] += gekauft - gebuehr
        self._orders += 1
        return Order(order_id=f"sim-{self._orders}", menge=gekauft - gebuehr, preis=kurs)

    def verkaufe(self, symbol: str, menge: float) -> Order:
        """Verkauft `menge` BTC zum selben Kurs wie kaufe(). Die Gebühr geht von den USDT ab."""
        if menge > self._konto["BTC"]:
            raise ValueError(
                f"Verkauf von {menge} BTC abgelehnt: auf dem Konto sind nur {self._konto['BTC']} BTC"
            )
        kurs = self.hole_kerzen(symbol, "", 1)[-1].schluss
        erloes = menge * kurs
        self._konto["BTC"] -= menge
        self._konto["USDT"] += erloes - erloes * self._gebuehr_prozent / 100
        self._orders += 1
        return Order(order_id=f"sim-{self._orders}", menge=menge, preis=kurs)


@dataclass(frozen=True)
class Ergebnis:
    kerzen_ausgewertet: int
    kaeufe: int
    verkaeufe: int
    startwert: float
    endwert: float
    rendite_prozent: float


def backtest(
    *,
    kerzen: list[Kerze],
    speicher: Speicher,
    einstellungen: Einstellungen,
    startguthaben: float = 1000.0,
) -> Ergebnis:
    """Lässt den echten tick() Kerze für Kerze über die aufgezeichneten Kurse laufen.

    Ausgewertet wird ab der n-ten Kerze, vorher reichen die Kurse nicht für die Bänder. Die Zeit
    springt jeweils ans Ende der Kerze: In diesem Moment ist sie laut polling.py abgeschlossen.
    """
    boerse = AufgezeichneteBoerse(
        kerzen=kerzen, intervall_dauer=einstellungen.intervall_dauer, startguthaben=startguthaben
    )
    ausgewertet = 0
    for kerze in kerzen[einstellungen.n - 1 :]:
        boerse.jetzt = kerze.startzeit + einstellungen.intervall_dauer
        signal = tick(
            boerse=boerse, speicher=speicher, einstellungen=einstellungen, jetzt=boerse.jetzt
        )
        if signal is not None:  # None: tick() hat nichts ausgewertet
            ausgewertet += 1

    seiten = [trade.seite for trade in speicher.hole_trades()]
    endwert = boerse.hole_kontostand("USDT") + boerse.hole_kontostand("BTC") * kerzen[-1].schluss
    return Ergebnis(
        kerzen_ausgewertet=ausgewertet,
        kaeufe=seiten.count("kauf"),
        verkaeufe=seiten.count("verkauf"),
        startwert=startguthaben,
        endwert=endwert,
        rendite_prozent=(endwert - startguthaben) / startguthaben * 100,
    )


def main(argv: list[str] | None = None) -> int:
    """Aufruf: `python -m bollinger_bot.backtest <kerzen.csv> [--db data/backtest.db]`."""
    parser = argparse.ArgumentParser(description="Backtest der Bollinger-Strategie")
    parser.add_argument("kerzen", help="CSV mit Kerzen, z.B. tests/daten/btcusdt_15min.csv")
    parser.add_argument("--db", default="data/backtest.db", help="neue SQLite-Datei")
    argumente = parser.parse_args(argv)
    # WARNING statt INFO: Die Zusammenfassung soll nicht zwischen den Käufen untergehen.
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if Path(argumente.db).exists():
        print(
            f"{argumente.db} existiert schon. Bitte eine neue Datei angeben, sonst mischen sich "
            "die Läufe.",
            file=sys.stderr,
        )
        return 2
    kerzen = lies_kerzen(argumente.kerzen)
    with Speicher(argumente.db) as speicher:
        ergebnis = backtest(kerzen=kerzen, speicher=speicher, einstellungen=Einstellungen())

    von, bis = kerzen[0].startzeit, kerzen[-1].startzeit
    print(f"Kerzen vom {von:%d.%m.%Y %H:%M} bis {bis:%d.%m.%Y %H:%M} UTC")
    print(f"Ausgewertete Kerzen: {ergebnis.kerzen_ausgewertet}")
    print(f"Käufe: {ergebnis.kaeufe}, Verkäufe: {ergebnis.verkaeufe}")
    print(f"Startwert: {ergebnis.startwert:.2f} USDT")
    print(f"Endwert: {ergebnis.endwert:.2f} USDT")
    print(f"Rendite: {ergebnis.rendite_prozent:.2f} %")
    return 0
