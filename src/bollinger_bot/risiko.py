"""Risiko-Check: wie viel der Bot kaufen oder verkaufen darf.

Reine Rechenlogik ohne I/O. `tick()` ruft sie auf, bevor eine Order an die Börse geht:

- **Kaufen:** Aus dem freien Guthaben in USDT wird ein Kaufbetrag in USDT. Bitget erwartet bei
  einer Market-Kauforder den Betrag in USDT, nicht die Menge in BTC.
- **Verkaufen:** Die Menge in BTC wird auf die Stellen abgerundet, die Bitget annimmt.

Liegt der Betrag unter dem Mindestbetrag von Bitget, ist das Ergebnis 0.0: `tick()` schickt dann
keine Order. Gerundet wird nur hier und immer abgerundet, damit der Bot nie mehr ausgibt oder
verkauft, als auf dem Konto liegt.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True)
class Handelsregeln:
    """Was Bitget für ein Handelspaar annimmt (aus `GET /api/v2/spot/public/symbols`)."""

    mengen_stellen: int  # Nachkommastellen der Menge in der Basiswährung (BTC), `quantityPrecision`
    betrag_stellen: int  # Nachkommastellen eines Betrags in USDT
    mindestbetrag: float  # kleinster Orderwert in USDT, `minTradeUSDT`


# Werte für BTCUSDT Spot im Demo-Modus, abgefragt am 30.09.2026:
# quantityPrecision 6, minTradeUSDT 1. Beträge in USDT runden wir auf Rappen (2 Stellen).
BTCUSDT_SPOT = Handelsregeln(mengen_stellen=6, betrag_stellen=2, mindestbetrag=1.0)


def abrunden(wert: float, stellen: int) -> float:
    """Rundet `wert` auf `stellen` Nachkommastellen ab, z.B. `abrunden(0.1239, 2)` ergibt 0.12.

    Rechnet über `Decimal` statt mit `math.floor(wert * 100) / 100`: `0.29 * 100` ergibt in
    `float` 28.999999999999996, und `floor` macht daraus 0.28 statt 0.29.
    """
    if stellen < 0:
        raise ValueError(f"stellen darf nicht negativ sein, erhalten: {stellen}")
    # str() liefert die kürzeste Schreibweise des float (0.29 statt 0.28999999999999998).
    schritt = Decimal(1).scaleb(-stellen)  # 10 hoch -stellen, z.B. 0.01 bei 2 Stellen
    return float(Decimal(str(wert)).quantize(schritt, rounding=ROUND_DOWN))


def kaufbetrag(
    *,
    guthaben: float,
    anteil_prozent: float,
    regeln: Handelsregeln,
    hoechstbetrag: float | None = None,
) -> float:
    """Betrag in USDT für eine Market-Kauforder, 0.0 wenn er unter dem Mindestbetrag liegt.

    - `guthaben`: frei verfügbares Guthaben in USDT (`Boerse.hole_kontostand("USDT")`)
    - `anteil_prozent`: welcher Anteil davon pro Kauf eingesetzt wird, z.B. 10 für 10 %
    - `hoechstbetrag`: optionale Obergrenze in USDT pro Kauf, unabhängig vom Guthaben
    """
    if guthaben < 0:
        raise ValueError(f"guthaben darf nicht negativ sein, erhalten: {guthaben}")
    if not 0 < anteil_prozent <= 100:
        raise ValueError(
            f"anteil_prozent muss grösser als 0 und höchstens 100 sein, erhalten: {anteil_prozent}"
        )
    if hoechstbetrag is not None and hoechstbetrag <= 0:
        raise ValueError(f"hoechstbetrag muss grösser als 0 sein, erhalten: {hoechstbetrag}")

    # Erst multiplizieren, dann teilen, wie in regeln.py: 1000 · 10 / 100 ergibt genau 100.0.
    betrag = guthaben * anteil_prozent / 100
    if hoechstbetrag is not None:
        betrag = min(betrag, hoechstbetrag)
    betrag = abrunden(betrag, regeln.betrag_stellen)

    if betrag < regeln.mindestbetrag:
        return 0.0
    return betrag


def verkaufsmenge(*, bestand: float, kurs: float, regeln: Handelsregeln) -> float:
    """Menge in der Basiswährung (BTC) für eine Market-Verkaufsorder, abgerundet.

    0.0, wenn der Wert der abgerundeten Menge (Menge · Kurs) unter dem Mindestbetrag liegt. Ein
    solcher Rest lässt sich bei Bitget nicht verkaufen.

    - `bestand`: gekaufte Menge laut Order, schon ohne Gebühr (`Order.menge`)
    - `kurs`: aktueller Kurs in USDT, z.B. der Schlusskurs der letzten Kerze
    """
    if bestand < 0:
        raise ValueError(f"bestand darf nicht negativ sein, erhalten: {bestand}")
    if kurs <= 0:
        raise ValueError(f"kurs muss grösser als 0 sein, erhalten: {kurs}")

    menge = abrunden(bestand, regeln.mengen_stellen)
    # Wert wie beim Abrunden über Decimal rechnen, damit die Grenze exakt gilt: Genau auf dem
    # Mindestbetrag ist der Verkauf noch erlaubt, ein Rappenbruchteil darunter nicht.
    wert = Decimal(str(menge)) * Decimal(str(kurs))
    if wert < Decimal(str(regeln.mindestbetrag)):
        return 0.0
    return menge
