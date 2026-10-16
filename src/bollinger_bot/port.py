"""Port: die Schnittstelle, über die `tick()` mit der Börse spricht.

`tick()` kennt nur diese Schnittstelle, nicht Bitget selbst. Der `BitgetClient` setzt sie um, in
den Tests ersetzt ein Mock sie. So lässt sich der Ablauf testen, ohne je eine echte Anfrage an
Bitget zu schicken.

`Protocol` beschreibt nur, welche Methoden es gibt und was sie annehmen und zurückgeben. Eine
Klasse erfüllt das Protocol, wenn sie diese Methoden hat, sie muss nicht davon erben.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Kerze:
    """Eine Kerze: Kursverlauf in einem festen Zeitabschnitt (z.B. 15 Minuten)."""

    startzeit: datetime  # Beginn des Zeitabschnitts, in UTC
    eroeffnung: float  # erster Kurs im Abschnitt
    hoch: float  # höchster Kurs
    tief: float  # tiefster Kurs
    schluss: float  # letzter Kurs, daraus rechnet die Bollinger-Strategie
    volumen: float  # gehandelte Menge im Abschnitt


@dataclass(frozen=True)
class Order:
    """Eine ausgeführte Order, wie die Börse sie bestätigt."""

    order_id: str  # Kennung der Order bei der Börse
    menge: float  # tatsächlich gekaufte oder verkaufte Menge, z.B. in BTC
    preis: float  # durchschnittlicher Ausführungspreis, z.B. in USDT pro BTC


class Boerse(Protocol):
    def hole_kerzen(self, symbol: str, intervall: str, anzahl: int) -> list[Kerze]:
        """Die letzten `anzahl` abgeschlossenen Kerzen, älteste zuerst.

        Beispiel: `hole_kerzen("BTCUSDT", "15min", 20)`. Die laufende, noch nicht abgeschlossene
        Kerze ist nicht dabei.
        """
        ...

    def hole_kontostand(self, waehrung: str) -> float:
        """Frei verfügbares Guthaben in einer Währung, z.B. `hole_kontostand("USDT")`."""
        ...

    def kaufe(self, symbol: str, menge: float) -> Order:
        """Market-Order: kauft `menge` sofort zum aktuellen Preis.

        Die Menge ist bereits vom Risiko-Check auf die erlaubten Stellen gerundet.
        """
        ...

    def verkaufe(self, symbol: str, menge: float) -> Order:
        """Market-Order: verkauft `menge` sofort zum aktuellen Preis."""
        ...
