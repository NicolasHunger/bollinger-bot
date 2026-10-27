"""tick(): ein Durchgang des Bots. Die Polling-Schleife ruft ihn jede Minute auf.

Meistens ist seit der letzten Auswertung noch keine neue Kerze fertig, dann endet tick() sofort,
ohne Bitget zu fragen. Sonst holt er die letzten n Kerzen und wertet die neueste aus. Liefert
Bitget die neue Kerze noch nicht, versucht er es eine Minute später nochmals. Aus den
Schlusskursen berechnet er die Bollinger-Bänder, und `entscheide()` sagt mit Blick auf die offene
Position, ob gekauft, verkauft oder gehalten wird. Wie viel gekauft oder verkauft wird, rechnet
der Risiko-Check, und jede ausgeführte Order wird sofort geloggt und als Trade gespeichert. Erst
danach kommen das Signal und der Kontostand in die Datenbank: Stürzt tick() nach dem Speichern
des Trades ab, wird dieselbe Kerze eine Minute später nochmals ausgewertet, und weil der Trade
schon gespeichert ist, kauft der Bot nicht doppelt. Fehler der Börse fängt tick() nicht ab, das
macht die Polling-Schleife.

Nicht automatisch behoben wird ein Abbruch genau zwischen der Order und dem Speichern des Trades
(Fehler, Absturz oder `KeyboardInterrupt`): Nach einem Kauf sieht das Sicherheitsnetz die BTC bei
Bitget und kauft nicht nochmals, nach einem Verkauf meldet tick(), dass Bitget die Position nicht
mehr hat. Beides als ERROR, die Order-ID steht im Log, den Trade trägt man von Hand nach. Dasselbe
gilt für den Rest eines nur teilweise ausgeführten Verkaufs. `KeyboardInterrupt` fängt tick()
bewusst nicht ab: So beendet `docker stop` (SIGINT) den Bot sofort.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from bollinger_bot.bollinger import Baender, berechne_baender
from bollinger_bot.polling import letzte_abgeschlossene_kerze, neue_kerze_fertig
from bollinger_bot.port import Boerse, Kerze, Order
from bollinger_bot.regeln import Signal, entscheide
from bollinger_bot.risiko import BTCUSDT_SPOT, Handelsregeln, kaufbetrag, verkaufsmenge
from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Einstellungen:
    """Womit und wie der Bot handelt. Die Standardwerte gelten für BTCUSDT mit 15-Minuten-Kerzen.

    Der Einstiegspunkt setzt `intervall` und `intervall_dauer` beide aus `bitget_client.INTERVALLE`,
    damit sie zusammenpassen.
    """

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
    """Wertet die neueste abgeschlossene Kerze aus und handelt danach.

    Gibt das Signal zurück, oder None, wenn es nichts auszuwerten gab. `jetzt` ist die aktuelle
    Zeit in UTC, als Argument übergeben, damit die Tests eine feste Zeit nutzen können.
    """
    if not neue_kerze_fertig(
        jetzt, einstellungen.intervall_dauer, speicher.letzte_signal_startzeit()
    ):
        return None
    kerzen = _hole_kerzen(boerse=boerse, einstellungen=einstellungen, jetzt=jetzt)
    if kerzen is None:
        return None

    kerze = kerzen[-1]
    baender = berechne_baender([k.schluss for k in kerzen], n=einstellungen.n, k=einstellungen.k)
    position = speicher.offene_position()
    signal = entscheide(
        schlusskurs=kerze.schluss,
        unteres_band=baender.unten,
        mittelband=baender.mitte,
        kaufpreis=position.preis if position else None,
        stop_loss_prozent=einstellungen.stop_loss_prozent,
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
        assert position is not None  # entscheide() verkauft nur mit offener Position
        _verkaufe(
            boerse=boerse,
            speicher=speicher,
            einstellungen=einstellungen,
            position=position,
            kurs=kerze.schluss,
            jetzt=jetzt,
        )

    # Erst Bitget fragen, dann speichern: Mit dem Signal gilt die Kerze als ausgewertet (siehe
    # Modul-Docstring), danach darf nichts mehr an Bitget scheitern.
    betrag = boerse.hole_kontostand(einstellungen.quotewaehrung)
    speicher.speichere_signal(_signal_eintrag(kerze, baender, signal))
    speicher.speichere_kontostand(
        Kontostand(zeit=jetzt, waehrung=einstellungen.quotewaehrung, betrag=betrag)
    )
    return signal


def _hole_kerzen(
    *, boerse: Boerse, einstellungen: Einstellungen, jetzt: datetime
) -> list[Kerze] | None:
    """Die letzten n Kerzen, oder None, wenn Bitget die neueste noch nicht liefert."""
    erwartet = letzte_abgeschlossene_kerze(jetzt, einstellungen.intervall_dauer)
    kerzen = boerse.hole_kerzen(einstellungen.symbol, einstellungen.intervall, einstellungen.n)
    if not kerzen or kerzen[-1].startzeit < erwartet:
        # Bitget legt eine neue Kerze erst 1–2 s nach dem Schluss an.
        log.info(
            "Bitget liefert die Kerze ab %s noch nicht, neuer Versuch im nächsten Durchgang",
            erwartet,
        )
        return None
    return kerzen


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
    if betrag <= 0:
        log.warning("Kaufsignal, aber zu wenig %s für eine Order", einstellungen.quotewaehrung)
        return

    order = boerse.kaufe(einstellungen.symbol, betrag)
    # Vor dem Speichern loggen: Scheitert es, steht die Order-ID wenigstens im Log.
    log.info(
        "Gekauft: Order %s, %s %s zu %s",
        order.order_id,
        order.menge,
        einstellungen.basiswaehrung,
        order.preis,
    )
    speicher.speichere_trade(_trade(order, seite="kauf", zeit=jetzt))


def _verkaufe(
    *,
    boerse: Boerse,
    speicher: Speicher,
    einstellungen: Einstellungen,
    position: Trade,
    kurs: float,
    jetzt: datetime,
) -> None:
    # Verkauft wird, was bei Bitget liegt, nicht die Menge laut Datenbank: Mehr lehnt Bitget ab,
    # und so verkauft der Bot auch die Reste, die beim Abrunden früherer Verkäufe liegen blieben.
    # Auf dem Demo-Konto gehören alle BTC dem Bot.
    bestand = boerse.hole_kontostand(einstellungen.basiswaehrung)
    menge = verkaufsmenge(bestand=bestand, kurs=kurs, regeln=einstellungen.regeln)
    if menge <= 0:
        if position.menge * kurs >= einstellungen.regeln.mindestbetrag:
            # Laut Datenbank eine echte Position, bei Bitget fast nichts: Wohl schon verkauft,
            # aber nicht gespeichert (z.B. Absturz zwischen verkaufe() und dem Speichern).
            log.error(
                "Verkaufssignal für die Position aus Order %s (%s %s), bei Bitget liegen aber "
                "nur %s %s. Wohl schon verkauft. Bitte den Verkauf von Hand nachtragen.",
                position.order_id,
                position.menge,
                einstellungen.basiswaehrung,
                bestand,
                einstellungen.basiswaehrung,
            )
        else:
            log.warning(
                "Verkaufssignal, aber %s %s sind weniger wert als der Mindestbetrag. "
                "Die Position bleibt offen.",
                bestand,
                einstellungen.basiswaehrung,
            )
        return

    order = boerse.verkaufe(einstellungen.symbol, menge)
    log.info(
        "Verkauft: Order %s, %s %s zu %s",
        order.order_id,
        order.menge,
        einstellungen.basiswaehrung,
        order.preis,
    )
    speicher.speichere_trade(_trade(order, seite="verkauf", zeit=jetzt))


def _trade(order: Order, *, seite: str, zeit: datetime) -> Trade:
    return Trade(
        zeit=zeit, seite=seite, menge=order.menge, preis=order.preis, order_id=order.order_id
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
