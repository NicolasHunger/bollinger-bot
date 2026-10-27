"""Kauf- und Verkaufsregeln der Bollinger-Strategie.

Reine Entscheidungslogik ohne I/O. Die Bänder berechnet `bollinger.py`, ob eine Position offen
ist, weiss der Aufrufer.

- Kaufen: Die Kerze schliesst unter dem unteren Band.
- Verkaufen: Die Kerze schliesst über dem Mittelband (Kurs ist zur Mitte zurückgekehrt) oder mehr
  als `stop_loss_prozent` unter dem Kaufpreis (Stop-Loss, begrenzt den Verlust).
- Es gibt höchstens eine offene Position: Mit offener Position wird nicht nachgekauft.
"""

from enum import Enum


class Signal(Enum):
    KAUFEN = "kaufen"
    VERKAUFEN = "verkaufen"
    HALTEN = "halten"


def entscheide(
    *,
    schlusskurs: float,
    unteres_band: float,
    mittelband: float,
    kaufpreis: float | None,
    stop_loss_prozent: float = 3.0,
) -> Signal:
    """Entscheidet nach einer abgeschlossenen Kerze, was der Bot tun soll.

    `kaufpreis` ist None, wenn keine Position offen ist. Der `*` erzwingt Namen beim Aufruf
    (`entscheide(schlusskurs=…, …)`), damit sich die vielen Kurse nicht verwechseln lassen.
    """
    if not 0 < stop_loss_prozent < 100:
        raise ValueError(
            f"stop_loss_prozent muss zwischen 0 und 100 liegen, erhalten: {stop_loss_prozent}"
        )

    if kaufpreis is None:
        if schlusskurs < unteres_band:
            return Signal.KAUFEN
        return Signal.HALTEN

    # Erst multiplizieren, dann teilen: 100 · 97 / 100 ergibt genau 97.0, ohne Rundungsfehler.
    stop_loss_kurs = kaufpreis * (100 - stop_loss_prozent) / 100
    if schlusskurs > mittelband or schlusskurs < stop_loss_kurs:
        return Signal.VERKAUFEN
    return Signal.HALTEN
