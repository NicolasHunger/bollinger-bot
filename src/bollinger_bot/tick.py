"""tick(): ein Durchgang des Bots."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from bollinger_bot.bollinger import Baender, berechne_baender
from bollinger_bot.polling import letzte_abgeschlossene_kerze, neue_kerze_fertig
from bollinger_bot.port import Boerse, Kerze
from bollinger_bot.regeln import Signal, entscheide
from bollinger_bot.risiko import BTCUSDT_SPOT, Handelsregeln, kaufbetrag, verkaufsmenge
from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade

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
    kerze = kerzen[-1]
    if kerze.startzeit < erwartet:
        log.info(
            "Bitget liefert die Kerze ab %s noch nicht, neuer Versuch im nächsten Durchgang",
            erwartet,
        )
        return None

    baender = berechne_baender([k.schluss for k in kerzen], n=einstellungen.n, k=einstellungen.k)
    position = speicher.offene_position()
    signal = entscheide(
        schlusskurs=kerze.schluss,
        unteres_band=baender.unten,
        mittelband=baender.mitte,
        kaufpreis=position.preis if position else None,
    )
    if signal is Signal.KAUFEN:
        _kaufe(
            boerse=boerse,
            speicher=speicher,
            einstellungen=einstellungen,
            kurs=kerze.schluss,
            jetzt=jetzt,
        )
    elif signal is Signal.VERKAUFEN:
        _verkaufe(
            boerse=boerse,
            speicher=speicher,
            einstellungen=einstellungen,
            position=position,
            kurs=kerze.schluss,
            jetzt=jetzt,
        )
    speicher.speichere_signal(_signal_eintrag(kerze, baender, signal))
    speicher.speichere_kontostand(
        Kontostand(
            zeit=jetzt,
            waehrung=einstellungen.quotewaehrung,
            betrag=boerse.hole_kontostand(einstellungen.quotewaehrung),
        )
    )
    return signal


def _kaufe(
    *,
    boerse: Boerse,
    speicher: Speicher,
    einstellungen: Einstellungen,
    kurs: float,
    jetzt: datetime,
) -> None:
    # Sicherheitsnetz: Liegen bei Bitget schon BTC im Wert des Mindestbetrags, gibt es eine
    # Position, die nicht in der Datenbank steht (z.B. Absturz zwischen kaufe() und dem
    # Speichern). Ein kleinerer Rest ist Staub, der sich nicht verkaufen lässt.
    bestand = boerse.hole_kontostand(einstellungen.basiswaehrung)
    if bestand * kurs >= einstellungen.regeln.mindestbetrag:
        log.error(
            "Kaufsignal, aber bei Bitget liegen schon %s %s, die nicht in der Datenbank stehen. "
            "Kein Kauf. Bitte von Hand prüfen.",
            bestand,
            einstellungen.basiswaehrung,
        )
        return
    betrag = kaufbetrag(
        guthaben=boerse.hole_kontostand(einstellungen.quotewaehrung),
        anteil_prozent=einstellungen.anteil_prozent,
        regeln=einstellungen.regeln,
    )
    if betrag == 0.0:
        log.warning("Kaufsignal, aber zu wenig %s für eine Order", einstellungen.quotewaehrung)
        return
    order = boerse.kaufe(einstellungen.symbol, betrag)
    speicher.speichere_trade(
        Trade(zeit=jetzt, seite="kauf", menge=order.menge, preis=order.preis, order_id=order.order_id)
    )


def _verkaufe(
    *,
    boerse: Boerse,
    speicher: Speicher,
    einstellungen: Einstellungen,
    position: Trade,
    kurs: float,
    jetzt: datetime,
) -> None:
    # Meldet Bitget weniger, als gekauft wurde, nur das verkaufen: Mehr lehnt Bitget ab.
    bestand = min(position.menge, boerse.hole_kontostand(einstellungen.basiswaehrung))
    menge = verkaufsmenge(bestand=bestand, kurs=kurs, regeln=einstellungen.regeln)
    order = boerse.verkaufe(einstellungen.symbol, menge)
    speicher.speichere_trade(
        Trade(
            zeit=jetzt, seite="verkauf", menge=order.menge, preis=order.preis, order_id=order.order_id
        )
    )


def _signal_eintrag(kerze: Kerze, baender: Baender, signal: Signal) -> SignalEintrag:
    """Übersetzt die Namen: `Kerze` sagt `schluss`, `Baender` sagt `mitte`, `oben`, `unten`."""
    return SignalEintrag(
        startzeit=kerze.startzeit,
        eroeffnung=kerze.eroeffnung,
        hoch=kerze.hoch,
        tief=kerze.tief,
        schlusskurs=kerze.schluss,
        mittelband=baender.mitte,
        oberes_band=baender.oben,
        unteres_band=baender.unten,
        signal=signal.value,
    )
