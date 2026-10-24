"""Integrationstests für den Backtest: der echte tick() mit simulierter Börse und SQLite-Datei.

Die kleinen Tests rechnen wie `tests/integration/test_tick.py` mit Kursen um 100 USDT, n = 4
Kerzen und k = 1, damit sich alles im Kopf nachrechnen lässt.
"""

import logging
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bollinger_bot.backtest import Ergebnis, backtest, lies_kerzen, main
from bollinger_bot.port import Kerze
from bollinger_bot.speicher import Speicher, Trade
from bollinger_bot.tick import Einstellungen

MIN_15 = timedelta(minutes=15)
ZWOELF_UHR = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
EINSTELLUNGEN = Einstellungen(n=4, k=1.0)
ECHTE_KERZEN = Path(__file__).parents[1] / "daten" / "btcusdt_15min.csv"


def kerzen(*schlusskurse):
    """15-Minuten-Kerzen ab 12:00 UTC. Eröffnung, Hoch und Tief liegen um den Schlusskurs."""
    return [
        Kerze(
            startzeit=ZWOELF_UHR + i * MIN_15,
            eroeffnung=schluss - 1,
            hoch=schluss + 2,
            tief=schluss - 3,
            schluss=schluss,
            volumen=1.0,
        )
        for i, schluss in enumerate(schlusskurse)
    ]


@pytest.fixture
def speicher(tmp_path):
    with Speicher(tmp_path / "backtest.db") as speicher:
        yield speicher


# Sieben Kerzen, ausgewertet werden die vierte bis siebte (vorher reichen die Kurse nicht für
# n = 4). Startguthaben 1000 USDT, 10 % pro Kauf, Gebühr 0.1 %.
#   4. Kerze, Kurse 106, 108, 106, 100: Mittelband 105, σ = 3, unteres Band 102 (wie KAUFSIGNAL
#      in test_tick.py). 100 < 102, keine Position: kaufen für 100 USDT.
#      100 / 100 = 1 BTC, ausgegeben 100 USDT, Gebühr 0.001 BTC: 0.999 BTC, 900 USDT.
#   5. Kerze, Kurse 108, 106, 100, 100: Mittelband 103.5. 100 liegt nicht darüber und nicht
#      unter dem Stop-Loss 100 · 97 / 100 = 97: halten.
#   6. Kerze, Kurse 106, 100, 100, 110: Mittelband 104. 110 > 104: verkaufen.
#      verkaufsmenge(0.999 BTC) = 0.999, Erlös 0.999 · 110 = 109.89 USDT,
#      Gebühr 0.10989 USDT: 900 + 109.78011 = 1009.78011 USDT, keine BTC.
#   7. Kerze, Kurse 100, 100, 110, 110: Mittelband 105, Abweichungen −5, −5, +5, +5: σ = 5,
#      unteres Band 100. 110 liegt nicht darunter, keine Position: halten.
EIN_KAUF_EIN_VERKAUF = (106, 108, 106, 100, 100, 110, 110)


def test_backtest_mit_einem_kauf_und_einem_verkauf(speicher):
    ergebnis = backtest(
        kerzen=kerzen(*EIN_KAUF_EIN_VERKAUF), speicher=speicher, einstellungen=EINSTELLUNGEN
    )

    # Endwert 1009.78011 USDT + 0 BTC · 110. Rendite (1009.78011 − 1000) / 1000 = 0.978011 %.
    assert ergebnis == Ergebnis(
        kerzen_ausgewertet=4,
        kaeufe=1,
        verkaeufe=1,
        startwert=1000.0,
        endwert=pytest.approx(1009.78011),
        rendite_prozent=pytest.approx(0.978011),
    )


def test_backtest_mit_anderem_startguthaben(speicher):
    # Wie oben, aber mit 500 USDT: 10 % sind 50 USDT, 50 / 100 = 0.5 BTC, Gebühr 0.0005 BTC,
    # auf dem Konto 0.4995 BTC und 450 USDT. Verkauf zu 110: 0.4995 · 110 = 54.945 USDT,
    # Gebühr 0.054945, gutgeschrieben 54.890055: 450 + 54.890055 = 504.890055 USDT.
    # Rendite (504.890055 − 500) / 500 = 0.978011 %, gleich wie mit 1000 USDT.
    ergebnis = backtest(
        kerzen=kerzen(*EIN_KAUF_EIN_VERKAUF),
        speicher=speicher,
        einstellungen=EINSTELLUNGEN,
        startguthaben=500.0,
    )

    assert ergebnis.startwert == 500.0
    assert ergebnis.endwert == pytest.approx(504.890055)
    assert ergebnis.rendite_prozent == pytest.approx(0.978011)
    assert [k.betrag for k in speicher.hole_kontostaende()] == pytest.approx(
        [450.0, 450.0, 504.890055, 504.890055]
    )


def test_backtest_speichert_pro_ausgewerteter_kerze_ein_signal(speicher):
    alle = kerzen(*EIN_KAUF_EIN_VERKAUF)

    backtest(kerzen=alle, speicher=speicher, einstellungen=EINSTELLUNGEN)

    signale = speicher.hole_signale()
    assert [s.startzeit for s in signale] == [k.startzeit for k in alle[3:]]
    assert [s.signal for s in signale] == ["kaufen", "halten", "verkaufen", "halten"]
    # Gehandelt wird am Ende der Kerze: Die 4. Kerze beginnt um 12:45 und endet um 13:00, die
    # 6. beginnt um 13:15 und endet um 13:30.
    assert speicher.hole_trades() == [
        Trade(
            zeit=ZWOELF_UHR + 4 * MIN_15, seite="kauf", menge=0.999, preis=100.0, order_id="sim-1"
        ),
        Trade(
            zeit=ZWOELF_UHR + 6 * MIN_15,
            seite="verkauf",
            menge=0.999,
            preis=110.0,
            order_id="sim-2",
        ),
    ]
    # Pro ausgewerteter Kerze eine Zeile mit den freien USDT (Rechnung siehe oben).
    assert [k.betrag for k in speicher.hole_kontostaende()] == pytest.approx(
        [900.0, 900.0, 1009.78011, 1009.78011]
    )


def _stundenkerzen():
    """Dieselben Kurse, aber eine Stunde Abstand statt 15 Minuten."""
    return [
        Kerze(
            startzeit=ZWOELF_UHR + i * timedelta(hours=1),
            eroeffnung=k.eroeffnung,
            hoch=k.hoch,
            tief=k.tief,
            schluss=k.schluss,
            volumen=k.volumen,
        )
        for i, k in enumerate(kerzen(*EIN_KAUF_EIN_VERKAUF))
    ]


@pytest.mark.parametrize(
    "reihe",
    [
        # Lücke: Die 5. Kerze fehlt, die Bänder enthielten Kurse, die nicht nebeneinander liegen.
        kerzen(*EIN_KAUF_EIN_VERKAUF)[:4] + kerzen(*EIN_KAUF_EIN_VERKAUF)[5:],
        # Doppelte Kerze: tick() sähe die zweite als schon ausgewertet an.
        kerzen(*EIN_KAUF_EIN_VERKAUF)[:4] + kerzen(*EIN_KAUF_EIN_VERKAUF)[3:],
        # Stunden-Kerzen mit 15-Minuten-Einstellungen: Der Backtest hielte jede Kerze schon nach
        # 15 Minuten für abgeschlossen und handelte zu einem Kurs, der erst 45 Minuten später
        # feststeht.
        _stundenkerzen(),
    ],
    ids=["luecke", "doppelt", "stundenkerzen"],
)
def test_backtest_lehnt_kerzen_mit_falschem_abstand_ab(speicher, reihe):
    with pytest.raises(ValueError, match="Abstand"):
        backtest(kerzen=reihe, speicher=speicher, einstellungen=EINSTELLUNGEN)
    # Geprüft wird vor dem ersten tick(): In der Datenbank landet nichts.
    assert speicher.hole_signale() == []


@pytest.mark.parametrize("anzahl", [0, 3])
def test_backtest_lehnt_zu_wenig_kerzen_ab(speicher, anzahl):
    # Mit n = 4 braucht schon die erste Auswertung 4 Kerzen.
    with pytest.raises(ValueError, match="Zu wenig Kerzen"):
        backtest(
            kerzen=kerzen(*EIN_KAUF_EIN_VERKAUF)[:anzahl],
            speicher=speicher,
            einstellungen=EINSTELLUNGEN,
        )


# --- Echte Kurse: 2000 BTCUSDT-Kerzen (15 min) von Bitget, 03.10. bis 24.10.2026 ---


@pytest.fixture(scope="module")
def echter_lauf(tmp_path_factory):
    """Ein Backtest mit den Standard-Einstellungen (n = 20, k = 2), für alle Tests unten."""
    kerzen = lies_kerzen(ECHTE_KERZEN)
    with Speicher(tmp_path_factory.mktemp("echt") / "backtest.db") as speicher:
        ergebnis = backtest(kerzen=kerzen, speicher=speicher, einstellungen=Einstellungen())
        yield kerzen, speicher, ergebnis


def test_echte_kurse_ein_signal_pro_kerze_ab_der_zwanzigsten(echter_lauf):
    kerzen, speicher, ergebnis = echter_lauf
    # 2000 Kerzen, die erste Auswertung braucht 20: ausgewertet werden die 20. bis 2000.,
    # also 2000 − 20 + 1 = 1981 Kerzen.
    assert len(kerzen) == 2000
    assert ergebnis.kerzen_ausgewertet == 1981
    assert len(speicher.hole_signale()) == 1981
    assert speicher.hole_signale()[0].startzeit == kerzen[19].startzeit


def test_echte_kurse_trades_abwechselnd_kauf_und_verkauf(echter_lauf):
    _, speicher, ergebnis = echter_lauf
    seiten = [trade.seite for trade in speicher.hole_trades()]
    # Höchstens eine Position: Auf jeden Kauf folgt ein Verkauf, bevor wieder gekauft wird.
    # Ist am Ende eine Position offen, gibt es einen Kauf mehr als Verkäufe.
    assert seiten == ["kauf", "verkauf"] * ergebnis.verkaeufe + ["kauf"] * (
        ergebnis.kaeufe - ergebnis.verkaeufe
    )
    assert ergebnis.kaeufe - ergebnis.verkaeufe in (0, 1)
    assert seiten.count("kauf") == ergebnis.kaeufe


def test_echte_kurse_kontostaende_nie_negativ(echter_lauf):
    _, speicher, _ = echter_lauf
    kontostaende = speicher.hole_kontostaende()
    # Eine Zeile pro ausgewerteter Kerze, und nie mehr ausgegeben, als da war.
    assert len(kontostaende) == 1981
    assert min(k.betrag for k in kontostaende) >= 0


def test_echte_kurse_endwert_passt_zur_datenbank(echter_lauf):
    kerzen, speicher, ergebnis = echter_lauf
    # Endwert = freie USDT + BTC · letzter Schlusskurs. Die freien USDT stehen in der letzten
    # Kontostand-Zeile, die BTC fast genau in der offenen Position: Dazu kommt höchstens ein
    # Rest unter 0.000001 BTC aus dem Abrunden beim Verkauf, bei rund 80 000 USDT/BTC also
    # weniger als 0.08 USDT.
    position = speicher.offene_position()
    btc = position.menge if position else 0.0
    usdt = speicher.hole_kontostaende()[-1].betrag
    differenz = ergebnis.endwert - (usdt + btc * kerzen[-1].schluss)
    assert 0 <= differenz < 0.000001 * kerzen[-1].schluss
    assert ergebnis.rendite_prozent == pytest.approx((ergebnis.endwert - 1000) / 1000 * 100)


def test_echte_kurse_charakterisierung(echter_lauf):
    # Aufgezeichnet beim ersten Lauf am 24.10.2026, nicht von Hand gerechnet: Der Test hält fest,
    # was der Bot auf diesen Kursen tut. Ändert sich eine Zahl, hat sich das Verhalten von tick(),
    # den Regeln oder dem Risiko-Check geändert. Dann prüfen, ob das gewollt ist, und die Zahlen
    # bewusst nachführen.
    _, _, ergebnis = echter_lauf
    assert (ergebnis.kaeufe, ergebnis.verkaeufe) == (40, 39)
    assert ergebnis.endwert == pytest.approx(993.0731804118101, abs=1e-6)


# --- Aufruf über die Kommandozeile ---


def test_main_gibt_eine_zusammenfassung_aus(tmp_path, capsys):
    db = tmp_path / "backtest.db"

    code = main([str(ECHTE_KERZEN), "--db", str(db)])

    assert code == 0
    ausgabe = capsys.readouterr().out
    # Zahlen wie im Charakterisierungstest oben: Rendite (993.07 − 1000) / 1000 = −0.69 %.
    assert "03.10.2026 18:45 bis 24.10.2026 14:30 UTC" in ausgabe
    assert "Ausgewertete Kerzen: 1981" in ausgabe
    assert "Käufe: 40" in ausgabe
    assert "Verkäufe: 39" in ausgabe
    assert "Startwert: 1000.00 USDT" in ausgabe
    assert "Endwert: 993.07 USDT" in ausgabe
    assert "Rendite: -0.69 %" in ausgabe
    # Die Datei bleibt für das Dashboard liegen.
    with Speicher(db) as speicher:
        assert len(speicher.hole_signale()) == 1981


def test_main_richtet_logging_ein(tmp_path, mocker):
    # Format wie in der Abmachung, aber erst ab WARNING: tick() loggt jeden Kauf und Verkauf als
    # INFO (79 Zeilen auf den echten Kursen), das verdeckt die Zusammenfassung. Warnungen und
    # Fehler aus tick() sollen sichtbar bleiben.
    basic_config = mocker.patch("logging.basicConfig")

    main([str(ECHTE_KERZEN), "--db", str(tmp_path / "backtest.db")])

    basic_config.assert_called_once_with(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


def test_main_bricht_ab_wenn_die_datenbank_schon_existiert(tmp_path, capsys):
    # In einer Datei aus einem früheren Lauf würden sich zwei Läufe mischen, und tick() würde
    # jede Kerze überspringen, die dort schon ausgewertet ist. Darum nie in eine bestehende Datei.
    db = tmp_path / "backtest.db"
    Speicher(db).schliessen()

    code = main([str(ECHTE_KERZEN), "--db", str(db)])

    assert code == 2
    assert "existiert schon" in capsys.readouterr().err
    with Speicher(db) as speicher:
        assert speicher.hole_signale() == []


def test_main_meldet_eine_fehlende_csv(tmp_path, capsys):
    db = tmp_path / "backtest.db"

    code = main([str(tmp_path / "gibt_es_nicht.csv"), "--db", str(db)])

    assert code == 2
    assert "gibt_es_nicht.csv" in capsys.readouterr().err
    # Ohne Kerzen keine leere Datei anlegen, sonst scheitert der nächste Versuch an ihr.
    assert not db.exists()


@pytest.mark.parametrize(
    ("inhalt", "meldung"),
    [
        ("", "Zu wenig Kerzen"),
        ("2026-10-03T18:45:00,1,1,1,1,1\n", "Zeit ohne Zeitzone"),
    ],
    ids=["nur_kopfzeile", "ohne_zeitzone"],
)
def test_main_meldet_eine_ungueltige_csv(tmp_path, capsys, inhalt, meldung):
    csv_datei = tmp_path / "kerzen.csv"
    csv_datei.write_text("startzeit,eroeffnung,hoch,tief,schluss,volumen\n" + inhalt)
    db = tmp_path / "backtest.db"

    code = main([str(csv_datei), "--db", str(db)])

    assert code == 2
    assert meldung in capsys.readouterr().err
    # Wie bei der fehlenden CSV: keine Datei anlegen, an der der nächste Versuch scheitert.
    assert not db.exists()


# runpy warnt, weil die Tests das Modul schon importiert haben. Auf der Kommandozeile mit
# `python -m` kommt die Warnung nicht.
@pytest.mark.filterwarnings("ignore:'bollinger_bot.backtest' found in sys.modules")
def test_aufruf_mit_python_m(tmp_path, monkeypatch, capsys):
    # Wie `python -m bollinger_bot.backtest <kerzen.csv> --db <datei>` auf der Kommandozeile:
    # main() liest die Argumente aus sys.argv, der Rückgabewert wird zum Exit-Code.
    db = tmp_path / "backtest.db"
    monkeypatch.setattr("sys.argv", ["backtest", str(ECHTE_KERZEN), "--db", str(db)])

    with pytest.raises(SystemExit) as ende:
        runpy.run_module("bollinger_bot.backtest", run_name="__main__")

    assert ende.value.code == 0
    assert "Ausgewertete Kerzen: 1981" in capsys.readouterr().out
