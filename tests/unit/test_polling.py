"""Unit-Tests für «Ist die Kerze fertig?» und die Polling-Schleife.

Keiner dieser Tests wartet echt: Die Zeit wird als Argument übergeben, und die Schleife bekommt
eine Fake-Uhr, deren `schlafe()` die Zeit nur vorwärts stellt.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bollinger_bot.polling import (
    letzte_abgeschlossene_kerze,
    neue_kerze_fertig,
    polling_schleife,
)

MIN_15 = timedelta(minutes=15)


def utc(stunde, minute, sekunde=0, tag=17):
    return datetime(2026, 10, tag, stunde, minute, sekunde, tzinfo=UTC)


class FakeUhr:
    """Uhr für Tests: steht still, bis `schlafe()` sie vorwärts stellt.

    Immer in UTC starten, siehe Test über die Zeitumstellung.
    """

    def __init__(self, start):
        self.zeit = start
        self.pausen = []

    def jetzt(self):
        return self.zeit

    def schlafe(self, sekunden):
        self.pausen.append(sekunden)
        self.zeit += timedelta(seconds=sekunden)


# --- Welche Kerze ist abgeschlossen? ---


@pytest.mark.parametrize(
    ("jetzt", "erwartet"),
    [
        (utc(12, 16), utc(12, 0)),  # Kerze ab 12:15 läuft noch
        (utc(12, 29, 59), utc(12, 0)),  # Grenzwert: 1 s bevor die Kerze ab 12:15 fertig ist
        (utc(12, 30), utc(12, 15)),  # Grenzwert: genau jetzt ist die Kerze ab 12:15 fertig
        (utc(12, 30, 1), utc(12, 15)),
    ],
)
def test_letzte_abgeschlossene_kerze_bei_15_minuten(jetzt, erwartet):
    assert letzte_abgeschlossene_kerze(jetzt, MIN_15) == erwartet


@pytest.mark.parametrize(
    ("intervall", "erwartet"),
    [
        # Um 12:16:30 läuft die Kerze ab …, abgeschlossen ist die ein Intervall davor
        (timedelta(minutes=1), utc(12, 15)),  # untere Grenze: läuft ab 12:16
        (timedelta(minutes=5), utc(12, 10)),  # läuft ab 12:15
        (timedelta(minutes=15), utc(12, 0)),  # läuft ab 12:15
        (timedelta(minutes=30), utc(11, 30)),  # läuft ab 12:00
        (timedelta(hours=1), utc(11, 0)),  # obere Grenze: läuft ab 12:00
    ],
)
def test_alle_intervalle_des_bitget_clients_sind_gueltig(intervall, erwartet):
    # Dieselben Intervalle wie in der Tabelle INTERVALLE im Bitget-Client (1min bis 1h)
    assert letzte_abgeschlossene_kerze(utc(12, 16, 30), intervall) == erwartet


def test_letzte_abgeschlossene_kerze_ueber_mitternacht():
    # Um 00:05 ist die letzte abgeschlossene Kerze die ab 23:45 am Vortag
    assert letzte_abgeschlossene_kerze(utc(0, 5, tag=18), MIN_15) == utc(23, 45, tag=17)


def test_zeit_in_schweizer_sommerzeit_ergibt_kerze_in_utc():
    # 14:16 Sommerzeit (UTC+2) ist 12:16 UTC
    jetzt = datetime(2026, 10, 17, 14, 16, tzinfo=timezone(timedelta(hours=2)))

    assert letzte_abgeschlossene_kerze(jetzt, MIN_15) == utc(12, 0)


@pytest.mark.parametrize(
    ("fold", "erwartet"),
    [
        # 02:30 Sommerzeit (UTC+2) ist 00:30 UTC: Kerze ab 00:30 läuft, fertig ist die ab 00:15
        (0, utc(0, 15, tag=25)),
        # 02:30 Winterzeit (UTC+1) ist 01:30 UTC: Kerze ab 01:30 läuft, fertig ist die ab 01:15
        (1, utc(1, 15, tag=25)),
    ],
)
def test_doppelte_uhrzeit_bei_der_zeitumstellung(fold, erwartet):
    # Am So 25.10.2026 stellt die Schweiz um 03:00 Sommerzeit auf 02:00 Winterzeit zurück: 02:30
    # gibt es zweimal. `fold=0` ist das erste Mal, `fold=1` das zweite.
    jetzt = datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Zurich"), fold=fold)

    assert letzte_abgeschlossene_kerze(jetzt, MIN_15) == erwartet


def test_zeit_ohne_zeitzone_wird_abgelehnt():
    with pytest.raises(ValueError, match="Zeit ohne Zeitzone"):
        letzte_abgeschlossene_kerze(datetime(2026, 10, 17, 12, 16), MIN_15)


@pytest.mark.parametrize(
    "intervall",
    [
        timedelta(0),
        timedelta(minutes=-15),
        timedelta(milliseconds=500),  # teilt die Stunde, gab früher einen ZeroDivisionError
        timedelta(seconds=1.5),  # teilt die Stunde, ist aber keine ganze Minute
        timedelta(seconds=59),  # knapp unter der unteren Grenze von 1 Minute
        timedelta(seconds=90),  # teilt die Stunde (40 Kerzen), ist aber keine ganze Minute
        timedelta(minutes=7),  # 60 / 7 geht nicht auf
        timedelta(minutes=61),  # knapp über der oberen Grenze von 1 Stunde
        timedelta(hours=4),
    ],
)
def test_ungueltiges_intervall_wird_abgelehnt(intervall):
    with pytest.raises(
        ValueError, match="Intervall muss aus ganzen Minuten bestehen und eine Stunde ohne Rest"
    ):
        letzte_abgeschlossene_kerze(utc(12, 16), intervall)


# --- Ist eine neue Kerze fertig? ---


def test_beim_ersten_start_ist_immer_eine_kerze_fertig():
    assert neue_kerze_fertig(utc(12, 16), MIN_15, zuletzt_ausgewertet=None) is True


def test_keine_neue_kerze_solange_die_naechste_noch_laeuft():
    # Kerze ab 12:00 ist ausgewertet, die ab 12:15 läuft bis 12:30
    assert neue_kerze_fertig(utc(12, 29, 59), MIN_15, zuletzt_ausgewertet=utc(12, 0)) is False


def test_neue_kerze_fertig_sobald_die_naechste_abgeschlossen_ist():
    assert neue_kerze_fertig(utc(12, 30), MIN_15, zuletzt_ausgewertet=utc(12, 0)) is True


# --- Polling-Schleife ---


def test_schleife_ruft_tick_auf_und_wartet_jeweils_60_sekunden():
    uhr = FakeUhr(utc(12, 0))
    aufrufe = []

    polling_schleife(lambda: aufrufe.append(uhr.jetzt()), schlafen=uhr.schlafe, durchlaeufe=3)

    assert aufrufe == [utc(12, 0), utc(12, 1), utc(12, 2)]
    assert uhr.pausen == [60, 60, 60]


def test_schleife_laeuft_nach_einem_fehler_weiter(caplog):
    uhr = FakeUhr(utc(12, 0))
    aufrufe = []

    def tick():
        aufrufe.append(uhr.jetzt())
        if len(aufrufe) == 1:
            raise ConnectionError("Bitget nicht erreichbar")

    with caplog.at_level(logging.ERROR):
        polling_schleife(tick, schlafen=uhr.schlafe, durchlaeufe=2)

    assert len(aufrufe) == 2
    assert "tick() fehlgeschlagen" in caplog.text
    assert "Bitget nicht erreichbar" in caplog.text


def test_keyboard_interrupt_beendet_die_schleife():
    # Docker stoppt den Bot mit `stop_signal: SIGINT`, das kommt als KeyboardInterrupt an. Die
    # Schleife darf ihn nicht verschlucken (`except Exception`, nie `except BaseException`).
    # `durchlaeufe=3`, damit der Test bei diesem Fehler rot wird, statt endlos zu laufen.
    uhr = FakeUhr(utc(12, 0))
    aufrufe = []

    def tick():
        aufrufe.append(uhr.jetzt())
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        polling_schleife(tick, schlafen=uhr.schlafe, durchlaeufe=3)

    assert len(aufrufe) == 1
    assert uhr.pausen == []  # danach nicht mehr geschlafen


def simuliere_betrieb(start, durchlaeufe):
    """Lässt die Schleife mit Fake-Uhr ab `start` laufen, in Sekundenbruchteilen.

    Der Fake-tick macht, was tick() später auch macht: nur auswerten, wenn eine neue Kerze fertig
    ist. Gibt pro Auswertung (Startzeit der Kerze, Zeitpunkt der Auswertung) zurück.
    """
    uhr = FakeUhr(start)
    ausgewertet = []

    def tick():
        zuletzt = ausgewertet[-1][0] if ausgewertet else None
        if neue_kerze_fertig(uhr.jetzt(), MIN_15, zuletzt):
            kerze = letzte_abgeschlossene_kerze(uhr.jetzt(), MIN_15)
            ausgewertet.append((kerze, uhr.jetzt()))

    polling_schleife(tick, schlafen=uhr.schlafe, durchlaeufe=durchlaeufe)
    return ausgewertet


def test_halbe_stunde_simuliert_wertet_jede_kerze_genau_einmal_aus():
    ausgewertet = simuliere_betrieb(utc(12, 1), durchlaeufe=30)  # 12:01 bis 12:30

    assert ausgewertet == [
        (utc(11, 45), utc(12, 1)),  # Start: die letzte fertige Kerze sofort auswerten
        (utc(12, 0), utc(12, 15)),
        (utc(12, 15), utc(12, 30)),
    ]


def test_vier_stunden_ueber_die_zeitumstellung_wertet_jede_kerze_genau_einmal_aus():
    # Am So 25.10.2026 um 01:00 UTC springt die Schweizer Uhr von 03:00 Sommerzeit auf 02:00
    # Winterzeit zurück. Die Kerzen ab 00:00 bis 00:45 UTC und ab 01:00 bis 01:45 UTC beginnen
    # also beide um 02:00 bis 02:45 Schweizer Zeit.
    # Die Fake-Uhr läuft in UTC, weil `+ timedelta` bei ZoneInfo auf der Wanduhr rechnet: 02:59
    # Sommerzeit plus 1 Minute ergibt 03:00 Winterzeit, das sind 61 echte Minuten, und die
    # Simulation würde eine Stunde überspringen.
    ausgewertet = simuliere_betrieb(utc(23, 1, tag=24), durchlaeufe=240)  # 23:01 bis 03:00 UTC
    kerzen = [kerze for kerze, _ in ausgewertet]

    # Erwartet: ab 22:45 UTC (beim Start sofort) bis 02:45 UTC alle 15 Minuten, also
    # 4 Stunden = 16 Abstände = 17 Kerzen.
    assert len(kerzen) == 17
    assert kerzen[0] == utc(22, 45, tag=24)
    assert kerzen[-1] == utc(2, 45, tag=25)
    assert all(b - a == MIN_15 for a, b in zip(kerzen, kerzen[1:]))  # keine doppelt, keine fehlt
