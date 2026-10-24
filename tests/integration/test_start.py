"""Integrationstests für den Start des Bots: starte() mit echter SQLite-Datei.

Der `BitgetClient` ist ein Autospec-Mock der Klasse: Er prüft auch, mit welchen Argumenten der
Client erstellt wird, und schickt nie eine Anfrage an Bitget. Gespeichert wird in eine echte Datei
in `tmp_path`. Die Fake-Uhr stellt die Zeit beim Warten nur vorwärts, kein Test wartet echt.
"""

import logging
import runpy
from datetime import UTC, datetime, timedelta

import pytest

from bollinger_bot.bitget_client import BitgetClient, BitgetFehler
from bollinger_bot.start import Konfiguration, lies_konfiguration, main, starte
from bollinger_bot.tick import Einstellungen

# Um 12:16 UTC ist die Kerze ab 12:00 die letzte abgeschlossene (15-Minuten-Kerzen).
JETZT = datetime(2026, 10, 24, 12, 16, tzinfo=UTC)
KEYS = {
    "BITGET_API_KEY": "fake-key",
    "BITGET_API_SECRET": "fake-secret",
    "BITGET_PASSPHRASE": "fake-passphrase",
}


class FakeUhr:
    """Uhr für Tests: steht still, bis `schlafe()` sie vorwärts stellt (wie in test_polling.py)."""

    def __init__(self, start):
        self.zeit = start
        self.pausen = []

    def jetzt(self):
        return self.zeit

    def schlafe(self, sekunden):
        self.pausen.append(sekunden)
        self.zeit += timedelta(seconds=sekunden)


@pytest.fixture
def client_fabrik(mocker):
    # Autospec der Klasse: Der Aufruf prüft die Argumente des Konstruktors, `return_value` ist der
    # Client mit denselben Methoden wie der echte.
    fabrik = mocker.create_autospec(BitgetClient)
    fabrik.return_value.hole_kerzen.return_value = []  # Bitget liefert noch keine neue Kerze
    return fabrik


@pytest.fixture
def db_pfad(tmp_path):
    return tmp_path / "data" / "bot.db"  # den Ordner data/ gibt es noch nicht


@pytest.fixture
def konfiguration(db_pfad):
    return Konfiguration(
        api_key="fake-key",
        api_secret="fake-secret",
        passphrase="fake-passphrase",
        db_pfad=db_pfad,
        einstellungen=Einstellungen(),  # BTCUSDT, 15min, n = 20
    )


@pytest.fixture
def uhr():
    return FakeUhr(JETZT)


def test_ein_durchlauf_fragt_bitget_und_legt_die_datenbank_an(
    client_fabrik, konfiguration, db_pfad, uhr
):
    starte(
        konfiguration,
        client_fabrik=client_fabrik,
        uhr=uhr.jetzt,
        schlafen=uhr.schlafe,
        durchlaeufe=1,
    )

    client_fabrik.assert_called_once_with(
        api_key="fake-key", api_secret="fake-secret", passphrase="fake-passphrase"
    )
    client = client_fabrik.return_value
    client.hole_kerzen.assert_called_once_with("BTCUSDT", "15min", 20)
    assert db_pfad.exists()
    assert uhr.pausen == [60]  # einmal eine Minute gewartet
    client.schliessen.assert_called_once_with()


def test_starte_nutzt_die_einstellungen_und_die_uhr_aus_den_argumenten(
    client_fabrik, db_pfad, uhr, caplog
):
    # Andere Werte als die Standardwerte: So fällt auf, wenn starte() sie nicht weitergibt.
    einstellungen = Einstellungen(intervall="5min", intervall_dauer=timedelta(minutes=5), n=10)
    konfiguration = Konfiguration(
        api_key="fake-key",
        api_secret="fake-secret",
        passphrase="fake-passphrase",
        db_pfad=db_pfad,
        einstellungen=einstellungen,
    )
    caplog.set_level(logging.INFO)

    starte(
        konfiguration,
        client_fabrik=client_fabrik,
        uhr=uhr.jetzt,
        schlafen=uhr.schlafe,
        durchlaeufe=1,
    )

    client_fabrik.return_value.hole_kerzen.assert_called_once_with("BTCUSDT", "5min", 10)
    # Um 12:16 (Fake-Uhr) ist die 5-Minuten-Kerze ab 12:10 die letzte abgeschlossene. tick()
    # meldet sie, weil hole_kerzen() nichts liefert. Mit der echten Uhr stünde hier eine andere.
    assert "Kerze ab 2026-10-24 12:10:00+00:00 noch nicht" in caplog.text


def test_starte_ohne_uhr_nutzt_die_echte_zeit_in_utc(client_fabrik, konfiguration, uhr, caplog):
    # Ohne Zeitzone bräche tick() vor hole_kerzen() mit einem ValueError ab, den die
    # Polling-Schleife als ERROR loggt. Der Bot liefe weiter, aber handelte nie.
    starte(konfiguration, client_fabrik=client_fabrik, schlafen=uhr.schlafe, durchlaeufe=1)

    client_fabrik.return_value.hole_kerzen.assert_called_once()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_ein_fehler_von_bitget_beendet_den_bot_nicht(client_fabrik, konfiguration, uhr):
    client = client_fabrik.return_value
    client.hole_kerzen.side_effect = BitgetFehler("Bitget nicht erreichbar")

    starte(
        konfiguration,
        client_fabrik=client_fabrik,
        uhr=uhr.jetzt,
        schlafen=uhr.schlafe,
        durchlaeufe=2,
    )

    # Beide Durchläufe fragen Bitget, der Fehler im ersten beendet die Schleife nicht.
    assert client.hole_kerzen.call_count == 2
    assert uhr.pausen == [60, 60]


def test_ctrl_c_beendet_den_bot_und_schliesst_den_client(client_fabrik, konfiguration, uhr):
    # Ctrl+C oder `docker stop` (stop_signal: SIGINT) kommen als KeyboardInterrupt an, meist
    # während der Bot wartet.
    def schlafen(sekunden):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        starte(konfiguration, client_fabrik=client_fabrik, uhr=uhr.jetzt, schlafen=schlafen)

    client_fabrik.return_value.schliessen.assert_called_once_with()


def test_start_steht_im_log_ohne_keys(client_fabrik, konfiguration, db_pfad, uhr, caplog):
    caplog.set_level(logging.INFO)

    starte(
        konfiguration,
        client_fabrik=client_fabrik,
        uhr=uhr.jetzt,
        schlafen=uhr.schlafe,
        durchlaeufe=1,
    )

    start_meldungen = [r.getMessage() for r in caplog.records if r.name == "bollinger_bot.start"]
    assert len(start_meldungen) == 1
    assert "BTCUSDT" in start_meldungen[0]
    assert "15min" in start_meldungen[0]
    assert str(db_pfad) in start_meldungen[0]
    assert "fake-" not in caplog.text  # kein Key, auch nicht in anderen Meldungen


# --- main(): was `python -m bollinger_bot` aufruft ---


@pytest.fixture
def starte_mock(mocker):
    # Autospec von starte(): main() wird geprüft, ohne dass der Bot wirklich startet.
    return mocker.create_autospec(starte)


def test_main_ohne_keys_meldet_den_fehler_und_gibt_2_zurueck(starte_mock, caplog):
    code = main({"BITGET_API_KEY": "fake-key"}, starte=starte_mock)

    assert code == 2
    starte_mock.assert_not_called()
    fehler = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(fehler) == 1
    assert "BITGET_API_SECRET" in fehler[0].getMessage()
    assert fehler[0].exc_info is None  # nur die Meldung, kein Traceback
    assert "fake-key" not in caplog.text


def test_main_startet_den_bot_mit_der_konfiguration_aus_der_umgebung(starte_mock):
    umgebung = KEYS | {"BOT_INTERVALL": "5min"}

    assert main(umgebung, starte=starte_mock) == 0

    starte_mock.assert_called_once_with(lies_konfiguration(umgebung))


def test_main_nach_ctrl_c_meldet_das_ende_und_gibt_0_zurueck(starte_mock, caplog):
    # Ctrl+C oder `docker stop` ist ein gewolltes Ende, kein Fehler: Exit-Code 0, kein Traceback.
    caplog.set_level(logging.INFO)
    starte_mock.side_effect = KeyboardInterrupt

    assert main(KEYS, starte=starte_mock) == 0

    assert "Bot beendet" in caplog.text


def test_main_richtet_das_logging_ein(starte_mock, mocker):
    # Abmachung: Nur der Einstiegspunkt konfiguriert das Logging, die Module loggen nur.
    basic_config = mocker.patch("logging.basicConfig")

    main({}, starte=starte_mock)

    basic_config.assert_called_once_with(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


def test_python_m_bollinger_bot_ohne_keys_endet_mit_code_2(monkeypatch):
    # Wie `python -m bollinger_bot` in einer leeren Umgebung, aber im selben Prozess.
    monkeypatch.setattr("os.environ", {})

    with pytest.raises(SystemExit) as ende:
        runpy.run_module("bollinger_bot", run_name="__main__")

    assert ende.value.code == 2
