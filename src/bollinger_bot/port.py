"""Port: die Schnittstelle, über die `tick()` mit der Börse spricht.

`tick()` kennt nur diese Schnittstelle, nicht Bitget selbst. Der `BitgetClient` setzt sie um, in
den Tests ersetzt ein Mock sie. So lässt sich der Ablauf testen, ohne je eine echte Anfrage an
Bitget zu schicken.

Gehandelt wird Spot: Der Bot kauft BTC direkt mit USDT und verkauft sie später wieder. Bei
Futures würde er dagegen nur auf den Kurs wetten und Positionen öffnen und schliessen.

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
    """Eine ausgeführte Order, wie die Börse sie bestätigt.

    Bitget bestätigt beim Platzieren nur die Order-ID. Der Client fragt danach den Status ab, bis
    die Order ausgeführt ist, und nimmt Menge und Preis von dort.

    Beim Kauf ist `menge` schon abzüglich Gebühr: Bitget zieht die 0.1 % direkt von den gekauften
    BTC ab. Genau diese Menge liegt danach auf dem Konto und lässt sich wieder verkaufen. Beim
    Verkauf zieht Bitget die Gebühr von den erhaltenen USDT ab, `menge` bleibt dort ganz.
    """

    order_id: str  # Kennung der Order bei der Börse
    menge: float  # tatsächlich gekaufte oder verkaufte Menge in BTC, beim Kauf ohne Gebühr
    preis: float  # durchschnittlicher Ausführungspreis in USDT pro BTC


class Boerse(Protocol):
    def hole_kerzen(self, symbol: str, intervall: str, anzahl: int) -> list[Kerze]:
        """Die letzten `anzahl` abgeschlossenen Kerzen, älteste zuerst.

        Beispiel: `hole_kerzen("BTCUSDT", "15min", 20)`. `intervall` ist im Spot-Format von
        Bitget, die gültigen Werte und wie lange eine Kerze dauert stehen in
        `bitget_client.INTERVALLE`.

        Die laufende, noch nicht abgeschlossene Kerze ist nicht dabei. Der Client filtert sie mit
        der Uhr von Bitget (`requestTime` aus der Antwort), nicht mit der lokalen Uhr: Bitget legt
        die neue Kerze erst 1–2 s nach dem Schluss an, und die Uhr des Rechners kann falsch gehen.
        """
        ...

    def hole_kontostand(self, waehrung: str) -> float:
        """Frei verfügbares Guthaben in einer Währung, z.B. `hole_kontostand("USDT")`."""
        ...

    def kaufe(self, symbol: str, betrag: float) -> Order:
        """Market-Order: kauft sofort für `betrag` USDT zum aktuellen Preis.

        Bitget will bei einer Market-Kauforder den Betrag in USDT, nicht die Menge in BTC: Wie
        viel BTC man bekommt, steht erst nach der Ausführung fest, in `Order.menge`. Der Betrag
        ist bereits vom Risiko-Check auf die erlaubten Stellen abgerundet.
        """
        ...

    def verkaufe(self, symbol: str, menge: float) -> Order:
        """Market-Order: verkauft sofort `menge` BTC zum aktuellen Preis.

        Die Menge stammt aus dem Kauf (`Order.menge`, schon ohne Gebühr) und ist bereits vom
        Risiko-Check auf die erlaubten Stellen abgerundet.
        """
        ...
