"""tick(): ein Durchgang des Bots."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from bollinger_bot.port import Boerse
from bollinger_bot.regeln import Signal
from bollinger_bot.risiko import BTCUSDT_SPOT, Handelsregeln
from bollinger_bot.speicher import Speicher


@dataclass(frozen=True)
class Einstellungen:
    """Womit und wie der Bot handelt. Die Standardwerte gelten für BTCUSDT mit 15-Minuten-Kerzen."""

    symbol: str = "BTCUSDT"  # Handelspaar bei Bitget
    basiswaehrung: str = "BTC"  # was gekauft und verkauft wird
    quotewaehrung: str = "USDT"  # womit bezahlt wird
    intervall: str = "15min"  # Kerzen-Intervall im Bitget-Format
    intervall_dauer: timedelta = timedelta(minutes=15)  # muss zu `intervall` passen
    n: int = 20  # Anzahl Kerzen für die Bänder
    k: float = 2.0  # Abstand der Bänder in Standardabweichungen
    anteil_prozent: float = 10.0  # Anteil des freien Guthabens pro Kauf
    stop_loss_prozent: float = 3.0
    regeln: Handelsregeln = BTCUSDT_SPOT


def tick(
    *, boerse: Boerse, speicher: Speicher, einstellungen: Einstellungen, jetzt: datetime
) -> Signal | None:
    return None
