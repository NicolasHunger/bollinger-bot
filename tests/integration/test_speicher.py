"""Integrationstests für den SQLite-Speicher.

Die Tests laufen gegen eine echte SQLite-Datei. `tmp_path` ist eine pytest-Fixture: ein leerer
Ordner, den pytest für jeden Test neu anlegt und danach wegräumt.
"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade

ZEIT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def pfad(tmp_path):
    return tmp_path / "bot.db"


@pytest.fixture
def speicher(pfad):
    with Speicher(pfad) as speicher:
        yield speicher


def signal(startzeit=ZEIT, signal="halten", schlusskurs=100.0):
    return SignalEintrag(
        startzeit=startzeit,
        schlusskurs=schlusskurs,
        mittelband=100.0,
        oberes_band=110.0,
        unteres_band=90.0,
        signal=signal,
    )


def trade(seite, order_id, preis=100.0, zeit=ZEIT):
    return Trade(zeit=zeit, seite=seite, menge=0.001, preis=preis, order_id=order_id)


# --- Neue Datenbank ---


def test_neue_datenbank_ist_leer(speicher):
    assert speicher.hole_signale() == []
    assert speicher.hole_trades() == []
    assert speicher.hole_kontostaende() == []
    assert speicher.letzte_signal_startzeit() is None
    assert speicher.offene_position() is None


def test_daten_ueberleben_einen_neustart(pfad):
    # Der Bot läuft im Container: Nach einem Neustart muss er seine offene Position noch kennen.
    with Speicher(pfad) as speicher:
        speicher.speichere_signal(signal())
        speicher.speichere_trade(trade("kauf", "A1"))

    with Speicher(pfad) as speicher:
        assert speicher.hole_signale() == [signal()]
        assert speicher.offene_position() == trade("kauf", "A1")


# --- Signale ---


def test_signal_wird_unveraendert_gespeichert_und_gelesen(speicher):
    eintrag = signal(signal="kaufen", schlusskurs=89.5)

    assert speicher.speichere_signal(eintrag) is True
    assert speicher.hole_signale() == [eintrag]


def test_dieselbe_kerze_wird_nur_einmal_gespeichert(speicher):
    # Die Polling-Schleife kann dieselbe Kerze zweimal sehen: der zweite Eintrag wird ignoriert.
    assert speicher.speichere_signal(signal(signal="kaufen")) is True
    assert speicher.speichere_signal(signal(signal="halten")) is False

    assert [s.signal for s in speicher.hole_signale()] == ["kaufen"]


def test_signale_kommen_nach_zeit_sortiert_zurueck(speicher):
    speicher.speichere_signal(signal(startzeit=ZEIT + timedelta(minutes=30)))
    speicher.speichere_signal(signal(startzeit=ZEIT))
    speicher.speichere_signal(signal(startzeit=ZEIT + timedelta(minutes=15)))

    assert [s.startzeit for s in speicher.hole_signale()] == [
        ZEIT,
        ZEIT + timedelta(minutes=15),
        ZEIT + timedelta(minutes=30),
    ]
    assert speicher.letzte_signal_startzeit() == ZEIT + timedelta(minutes=30)


def test_ungueltiges_signal_wird_von_der_datenbank_abgelehnt(speicher):
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_signal(signal(signal="vielleicht"))

    assert speicher.hole_signale() == []


# --- Zeitzonen ---


def test_zeit_in_anderer_zeitzone_wird_als_utc_gespeichert(speicher):
    # 14:00 Schweizer Sommerzeit (UTC+2) ist 12:00 UTC
    sommerzeit = timezone(timedelta(hours=2))
    speicher.speichere_signal(signal(startzeit=datetime(2026, 10, 17, 14, 0, tzinfo=sommerzeit)))

    gelesen = speicher.letzte_signal_startzeit()
    assert gelesen == ZEIT
    assert gelesen.utcoffset() == timedelta(0)


def test_zeit_ohne_zeitzone_wird_abgelehnt(speicher):
    # Ohne Zeitzone ist unklar, ob Schweizer Zeit oder UTC gemeint ist.
    with pytest.raises(ValueError, match="Zeit ohne Zeitzone"):
        speicher.speichere_signal(signal(startzeit=datetime(2026, 10, 17, 12, 0)))


# --- Trades und offene Position ---


def test_nach_einem_kauf_ist_die_position_offen(speicher):
    speicher.speichere_trade(trade("kauf", "A1", preis=60_000))

    position = speicher.offene_position()
    assert position is not None
    assert position.preis == 60_000


def test_nach_dem_verkauf_ist_keine_position_offen(speicher):
    speicher.speichere_trade(trade("kauf", "A1"))
    speicher.speichere_trade(trade("verkauf", "A2"))

    assert speicher.offene_position() is None


def test_offene_position_ist_immer_der_letzte_kauf(speicher):
    speicher.speichere_trade(trade("kauf", "A1", preis=60_000))
    speicher.speichere_trade(trade("verkauf", "A2", preis=61_000))
    speicher.speichere_trade(trade("kauf", "A3", preis=59_000))

    assert speicher.offene_position() == trade("kauf", "A3", preis=59_000)
    assert [t.order_id for t in speicher.hole_trades()] == ["A1", "A2", "A3"]


def test_ungueltige_seite_wird_abgelehnt(speicher):
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_trade(trade("leerverkauf", "A1"))


def test_dieselbe_order_wird_nicht_doppelt_gespeichert(speicher):
    # Eine doppelte Order-ID deutet auf einen Fehler in tick() hin und soll auffallen.
    speicher.speichere_trade(trade("kauf", "A1"))

    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_trade(trade("kauf", "A1"))


# --- Kontostände ---


def test_kontostaende_kommen_nach_zeit_sortiert_zurueck(speicher):
    spaeter = Kontostand(zeit=ZEIT + timedelta(hours=1), waehrung="USDT", betrag=990.0)
    frueher = Kontostand(zeit=ZEIT, waehrung="USDT", betrag=1000.0)
    speicher.speichere_kontostand(spaeter)
    speicher.speichere_kontostand(frueher)

    assert speicher.hole_kontostaende() == [frueher, spaeter]
