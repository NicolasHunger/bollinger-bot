"""Bollinger-Bänder: Mittelband und zwei Bänder im Abstand von k Standardabweichungen.

Reine Rechenlogik ohne I/O. Woher die Schlusskurse kommen, entscheidet der Aufrufer.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Baender:
    """Ergebnis einer Berechnung. `frozen`: Die Werte lassen sich nachträglich nicht ändern."""

    mitte: float
    oben: float
    unten: float


def berechne_baender(schlusskurse: Sequence[float], n: int = 20, k: float = 2.0) -> Baender:
    """Berechnet die Bollinger-Bänder aus den letzten n Schlusskursen.

    - Mittelband: Durchschnitt der letzten n Kurse
    - Oberes und unteres Band: Mittelband ± k · σ
    - σ: Standardabweichung derselben n Kurse, geteilt durch n (nicht durch n − 1)

    Ältere Kurse vor den letzten n werden ignoriert.
    """
    if n < 2:
        raise ValueError(f"n muss mindestens 2 sein, erhalten: {n}")
    if k <= 0:
        raise ValueError(f"k muss grösser als 0 sein, erhalten: {k}")
    if len(schlusskurse) < n:
        raise ValueError(f"Mindestens {n} Schlusskurse nötig, erhalten: {len(schlusskurse)}")

    fenster = schlusskurse[-n:]
    mitte = sum(fenster) / n
    # Varianz: mittlere quadrierte Abweichung vom Mittelband. Geteilt durch n, weil die n Kurse
    # alles sind, was wir betrachten, und keine Stichprobe aus einer grösseren Menge.
    varianz = sum((kurs - mitte) ** 2 for kurs in fenster) / n
    sigma = math.sqrt(varianz)

    return Baender(mitte=mitte, oben=mitte + k * sigma, unten=mitte - k * sigma)
