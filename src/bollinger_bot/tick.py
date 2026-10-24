"""tick(): ein Durchgang des Bots."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from bollinger_bot.polling import letzte_abgeschlossene_kerze, neue_kerze_fertig
from bollinger_bot.port import Boerse
from bollinger_bot.regeln import Signal
from bollinger_bot.risiko import BTCUSDT_SPOT, Handelsregeln
from bollinger_bot.speicher import Speicher

log = logging.getLogger(__name__)


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
    if not neue_kerze_fertig(
        jetzt, einstellungen.intervall_dauer, speicher.letzte_signal_startzeit()
    ):
        return None

    erwartet = letzte_abgeschlossene_kerze(jetzt, einstellungen.intervall_dauer)
    kerzen = boerse.hole_kerzen(einstellungen.symbol, einstellungen.intervall, einstellungen.n)
    if kerzen[-1].startzeit < erwartet:
        log.info(
            "Bitget liefert die Kerze ab %s noch nicht, neuer Versuch im nächsten Durchgang",
            erwartet,
        )
        return None
    return None
