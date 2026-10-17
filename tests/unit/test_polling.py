"""Unit-Tests für «Ist die Kerze fertig?» und die Polling-Schleife.

Keiner dieser Tests wartet echt: Die Zeit wird als Argument übergeben, und die Schleife bekommt
eine Fake-Uhr, deren `schlafe()` die Zeit nur vorwärts stellt.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone

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
    """Uhr für Tests: steht still, bis `schlafe()` sie vorwärts stellt."""

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


def test_letzte_abgeschlossene_kerze_bei_1_minute():
    assert letzte_abgeschlossene_kerze(utc(12, 16, 30), timedelta(minutes=1)) == utc(12, 15)


def test_letzte_abgeschlossene_kerze_ueber_mitternacht():
    # Um 00:05 ist die letzte abgeschlossene Kerze die ab 23:45 am Vortag
    assert letzte_abgeschlossene_kerze(utc(0, 5, tag=18), MIN_15) == utc(23, 45, tag=17)


def test_zeit_in_schweizer_sommerzeit_ergibt_kerze_in_utc():
    # 14:16 Sommerzeit (UTC+2) ist 12:16 UTC
    jetzt = datetime(2026, 10, 17, 14, 16, tzinfo=timezone(timedelta(hours=2)))

    assert letzte_abgeschlossene_kerze(jetzt, MIN_15) == utc(12, 0)


def test_zeit_ohne_zeitzone_wird_abgelehnt():
    with pytest.raises(ValueError, match="Zeit ohne Zeitzone"):
        letzte_abgeschlossene_kerze(datetime(2026, 10, 17, 12, 16), MIN_15)


@pytest.mark.parametrize(
    "intervall", [timedelta(0), timedelta(minutes=-15), timedelta(minutes=7), timedelta(hours=4)]
)
def test_ungueltiges_intervall_wird_abgelehnt(intervall):
    with pytest.raises(ValueError, match="Intervall muss eine Stunde ohne Rest teilen"):
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


def test_halbe_stunde_simuliert_wertet_jede_kerze_genau_einmal_aus():
    # Simuliert 30 Minuten Betrieb ab 12:01 in Sekundenbruchteilen. Der Fake-tick macht, was
    # tick() später auch macht: nur auswerten, wenn eine neue Kerze fertig ist.
    uhr = FakeUhr(utc(12, 1))
    ausgewertet = []  # (Startzeit der Kerze, Zeitpunkt der Auswertung)

    def tick():
        zuletzt = ausgewertet[-1][0] if ausgewertet else None
        if neue_kerze_fertig(uhr.jetzt(), MIN_15, zuletzt):
            kerze = letzte_abgeschlossene_kerze(uhr.jetzt(), MIN_15)
            ausgewertet.append((kerze, uhr.jetzt()))

    polling_schleife(tick, schlafen=uhr.schlafe, durchlaeufe=30)  # 12:01 bis 12:30

    assert ausgewertet == [
        (utc(11, 45), utc(12, 1)),  # Start: die letzte fertige Kerze sofort auswerten
        (utc(12, 0), utc(12, 15)),
        (utc(12, 15), utc(12, 30)),
    ]
