"""Integrationstests für tick(): ein Durchgang mit echter SQLite-Datei und gemockter Börse.

Die Börse ist ein Autospec-Mock des Ports (`Boerse`), Bitget wird nie gefragt. Gespeichert wird in
eine echte Datei in `tmp_path`, so prüfen die Tests auch die Regeln der Datenbank mit.

Die Kurse liegen um 100 USDT statt um 60 000, damit sich die Bänder im Kopf nachrechnen lassen.
Die Tests rechnen mit n = 4 Kerzen und k = 1: Mit dem Standard k = 2 kann ein einzelner Ausreisser
erst ab n = 6 unter das untere Band fallen.
"""

from datetime import UTC, datetime, timedelta

import pytest

from bollinger_bot.port import Boerse
from bollinger_bot.speicher import SignalEintrag, Speicher
from bollinger_bot.tick import Einstellungen, tick

MIN_15 = timedelta(minutes=15)
# Um 12:16 UTC läuft die Kerze ab 12:15, die letzte abgeschlossene beginnt um 12:00.
JETZT = datetime(2026, 10, 24, 12, 16, tzinfo=UTC)
LETZTE_KERZE = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
EINSTELLUNGEN = Einstellungen(n=4, k=1.0)


@pytest.fixture
def boerse(mocker):
    return mocker.create_autospec(Boerse, instance=True)


@pytest.fixture
def speicher(tmp_path):
    with Speicher(tmp_path / "bot.db") as speicher:
        yield speicher


def signal_eintrag(startzeit, signal="halten"):
    return SignalEintrag(
        startzeit=startzeit,
        eroeffnung=100.0,
        hoch=101.0,
        tief=99.0,
        schlusskurs=100.0,
        mittelband=100.0,
        oberes_band=102.0,
        unteres_band=98.0,
        signal=signal,
    )


def test_ohne_neue_kerze_wird_bitget_nicht_gefragt(boerse, speicher):
    # Die Kerze ab 12:00 ist schon ausgewertet, die nächste (ab 12:15) läuft noch bis 12:30.
    speicher.speichere_signal(signal_eintrag(LETZTE_KERZE))

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is None
    assert boerse.method_calls == []
