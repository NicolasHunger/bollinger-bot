"""Integrationstests für tick(): ein Durchgang mit echter SQLite-Datei und gemockter Börse.

Die Börse ist ein Autospec-Mock des Ports (`Boerse`), Bitget wird nie gefragt. Gespeichert wird in
eine echte Datei in `tmp_path`, so prüfen die Tests auch die Regeln der Datenbank mit.

Die Kurse liegen um 100 USDT statt um 60 000, damit sich die Bänder im Kopf nachrechnen lassen.
Die Tests rechnen mit n = 4 Kerzen und k = 1: Mit dem Standard k = 2 kann ein einzelner Ausreisser
erst ab n = 6 unter das untere Band fallen.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from bollinger_bot.port import Boerse, Kerze, Order
from bollinger_bot.regeln import Signal
from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade
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


def kerzen(*schlusskurse, letzte=LETZTE_KERZE):
    """Kerzen im Abstand von 15 Minuten mit diesen Schlusskursen, die letzte beginnt um `letzte`.

    Eröffnung, Hoch und Tief liegen um den Schlusskurs herum und sind alle verschieden, damit
    auffällt, wenn tick() zwei davon vertauscht.
    """
    anzahl = len(schlusskurse)
    return [
        Kerze(
            startzeit=letzte - (anzahl - 1 - i) * MIN_15,
            eroeffnung=schluss - 1,
            hoch=schluss + 2,
            tief=schluss - 3,
            schluss=schluss,
            volumen=1.0,
        )
        for i, schluss in enumerate(schlusskurse)
    ]


def guthaben_bei_bitget(boerse, **guthaben):
    """Was `hole_kontostand()` liefert, z.B. `guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0)`.

    Gibt das Dictionary zurück: Ein Test kann es ändern, z.B. nach einer Order.
    """
    boerse.hole_kontostand.side_effect = lambda waehrung: guthaben[waehrung]
    return guthaben


def position_in_der_datenbank(speicher, *, preis, menge=0.999):
    """Ein früherer Kauf ohne Verkauf danach: Die Position ist offen."""
    kauf = Trade(zeit=JETZT - MIN_15 * 8, seite="kauf", menge=menge, preis=preis, order_id="kauf-1")
    speicher.speichere_trade(kauf)
    return kauf


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


def test_neue_kerze_noch_nicht_bei_bitget(boerse, speicher, caplog):
    # Ausgewertet ist die Kerze ab 11:45, um 12:16 ist die Kerze ab 12:00 fertig. Bitget legt sie
    # aber erst ein bis zwei Sekunden nach dem Schluss an und liefert als neueste noch 11:45.
    caplog.set_level(logging.INFO)
    speicher.speichere_signal(signal_eintrag(LETZTE_KERZE - MIN_15))
    boerse.hole_kerzen.return_value = kerzen(98, 102, 98, 102, letzte=LETZTE_KERZE - MIN_15)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is None
    boerse.hole_kerzen.assert_called_once_with("BTCUSDT", "15min", 4)
    assert speicher.letzte_signal_startzeit() == LETZTE_KERZE - MIN_15
    assert speicher.hole_kontostaende() == []
    assert "noch nicht" in caplog.text


def test_halten_speichert_signal_und_kontostand(boerse, speicher):
    # Erster Start, die Datenbank ist leer. Schlusskurse 98, 102, 98, 102:
    #   Mittelband = (98 + 102 + 98 + 102) / 4 = 100
    #   Abweichungen −2, +2, −2, +2, Quadrate je 4: Varianz = 16 / 4 = 4, σ = 2
    #   k = 1: oberes Band = 100 + 2 = 102, unteres Band = 100 − 2 = 98
    # Schlusskurs 102 liegt nicht unter 98, keine Position offen: halten.
    boerse.hole_kerzen.return_value = kerzen(98, 102, 98, 102)
    guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.HALTEN
    # Eröffnung 102 − 1, Hoch 102 + 2, Tief 102 − 3 (siehe kerzen()).
    assert speicher.hole_signale() == [
        SignalEintrag(
            startzeit=LETZTE_KERZE,
            eroeffnung=101.0,
            hoch=104.0,
            tief=99.0,
            schlusskurs=102.0,
            mittelband=100.0,
            oberes_band=102.0,
            unteres_band=98.0,
            signal="halten",
        )
    ]
    assert speicher.hole_kontostaende() == [Kontostand(zeit=JETZT, waehrung="USDT", betrag=1000.0)]
    assert speicher.hole_trades() == []
    boerse.kaufe.assert_not_called()
    boerse.verkaufe.assert_not_called()


# Schlusskurse für ein Kaufsignal:
#   Mittelband = (106 + 108 + 106 + 100) / 4 = 420 / 4 = 105
#   Abweichungen +1, +3, +1, −5, Quadrate 1 + 9 + 1 + 25 = 36: Varianz = 36 / 4 = 9, σ = 3
#   k = 1: oberes Band = 108, unteres Band = 102
# Ohne Position: Schlusskurs 100 liegt unter 102, also kaufen.
KAUFSIGNAL = (106, 108, 106, 100)


def test_kaufen_kauft_und_speichert_den_trade(boerse, speicher):
    boerse.hole_kerzen.return_value = kerzen(*KAUFSIGNAL)
    konto = guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0)

    def kauf_bei_bitget(symbol, betrag):
        # 100 USDT zu 100 USDT/BTC ergeben 1 BTC, Bitget zieht 0.1 % Gebühr ab: 0.999 BTC.
        konto["USDT"] -= betrag
        konto["BTC"] += 0.999
        return Order(order_id="kauf-1", menge=0.999, preis=100.0)

    boerse.kaufe.side_effect = kauf_bei_bitget

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.KAUFEN
    # Kaufbetrag: 10 % von 1000 USDT = 100.00 USDT
    boerse.kaufe.assert_called_once_with("BTCUSDT", 100.0)
    assert speicher.hole_trades() == [
        Trade(zeit=JETZT, seite="kauf", menge=0.999, preis=100.0, order_id="kauf-1")
    ]
    [eintrag] = speicher.hole_signale()
    assert eintrag.signal == "kaufen"
    assert (eintrag.mittelband, eintrag.oberes_band, eintrag.unteres_band) == (105.0, 108.0, 102.0)
    # Der Kontostand wird nach dem Kauf gelesen: 1000 − 100 = 900 USDT
    assert speicher.hole_kontostaende() == [Kontostand(zeit=JETZT, waehrung="USDT", betrag=900.0)]


def test_kaufen_ohne_order_wenn_der_betrag_unter_dem_mindestbetrag_liegt(boerse, speicher, caplog):
    # 10 % von 5 USDT = 0.50 USDT, unter dem Mindestbetrag von 1 USDT: kaufbetrag() ergibt 0.0.
    boerse.hole_kerzen.return_value = kerzen(*KAUFSIGNAL)
    guthaben_bei_bitget(boerse, USDT=5.0, BTC=0.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.KAUFEN
    boerse.kaufe.assert_not_called()
    assert speicher.hole_trades() == []
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    # Das Signal wird trotzdem gespeichert, sonst würde dieselbe Kerze jede Minute neu ausgewertet.
    assert [e.signal for e in speicher.hole_signale()] == ["kaufen"]


def test_kein_kauf_wenn_bei_bitget_schon_btc_liegen(boerse, speicher, caplog):
    # Die Datenbank kennt keine Position, bei Bitget liegen aber 0.01 BTC. Wert beim Schlusskurs:
    # 0.01 · 100 = 1.00 USDT, genau der Mindestbetrag: Das ist eine echte Position, die nicht in
    # der Datenbank steht (z.B. Absturz zwischen Kauf und Speichern). Nicht nochmals kaufen.
    boerse.hole_kerzen.return_value = kerzen(*KAUFSIGNAL)
    guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.01)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.KAUFEN
    boerse.kaufe.assert_not_called()
    assert speicher.hole_trades() == []
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert [e.signal for e in speicher.hole_signale()] == ["kaufen"]


def test_ein_rest_unter_dem_mindestbetrag_blockiert_den_kauf_nicht(boerse, speicher):
    # 0.0099 BTC · 100 = 0.99 USDT, knapp unter dem Mindestbetrag: Staub, wie er nach einem
    # Verkauf liegen bleiben kann. Er lässt sich nicht verkaufen und blockiert den Kauf nicht.
    boerse.hole_kerzen.return_value = kerzen(*KAUFSIGNAL)
    guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0099)
    boerse.kaufe.return_value = Order(order_id="kauf-1", menge=0.999, preis=100.0)

    tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    boerse.kaufe.assert_called_once_with("BTCUSDT", 100.0)


# Schlusskurse für ein Verkaufssignal über dem Mittelband, wie im Test für «halten»:
#   Mittelband 100, σ = 2, k = 1: oberes Band 102, unteres Band 98
# Mit offener Position: Schlusskurs 102 liegt über dem Mittelband 100, also verkaufen.
UEBER_DEM_MITTELBAND = (98, 102, 98, 102)


def test_verkaufen_verkauft_die_position_und_speichert_den_trade(boerse, speicher):
    # Gekauft zu 97 USDT. Bei Bitget liegen 0.0005 BTC mehr als gekauft (Staub von früher):
    # Verkauft wird nur die Menge der Position, 0.999 BTC.
    kauf = position_in_der_datenbank(speicher, preis=97.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(*UEBER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.9995)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.999, preis=102.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.VERKAUFEN
    # Wert 0.999 · 102 = 101.90 USDT, über dem Mindestbetrag: verkaufsmenge() lässt 0.999 stehen.
    boerse.verkaufe.assert_called_once_with("BTCUSDT", 0.999)
    assert speicher.hole_trades() == [
        kauf,
        Trade(zeit=JETZT, seite="verkauf", menge=0.999, preis=102.0, order_id="verkauf-1"),
    ]
    assert speicher.offene_position() is None
    assert [e.signal for e in speicher.hole_signale()] == ["verkaufen"]


def test_verkaufen_hoechstens_was_bei_bitget_liegt(boerse, speicher):
    # Gekauft wurden laut Datenbank 0.999 BTC, Bitget meldet nur 0.998 BTC. Mehr zu verkaufen,
    # als auf dem Konto liegt, lehnt Bitget ab: Verkauft wird min(0.999, 0.998) = 0.998 BTC.
    position_in_der_datenbank(speicher, preis=97.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(*UEBER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.998)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.998, preis=102.0)

    tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    boerse.verkaufe.assert_called_once_with("BTCUSDT", 0.998)


# Schlusskurse, bei denen nur der Stop-Loss verkauft:
#   Mittelband = (102 + 98 + 102 + 98) / 4 = 100, σ = 2, k = 1: unteres Band 98
# Schlusskurs 98 liegt nicht über dem Mittelband, ein Verkauf kommt also nur vom Stop-Loss.
UNTER_DEM_MITTELBAND = (102, 98, 102, 98)


def test_stop_loss_verkauft(boerse, speicher):
    # Gekauft zu 105 USDT. Stop-Loss 3 %: 105 · 97 / 100 = 101.85, Schlusskurs 98 liegt darunter.
    position_in_der_datenbank(speicher, preis=105.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(*UNTER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.999)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.999, preis=98.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.VERKAUFEN
    # Wert 0.999 · 98 = 97.90 USDT, über dem Mindestbetrag.
    boerse.verkaufe.assert_called_once_with("BTCUSDT", 0.999)
    assert speicher.offene_position() is None


def test_stop_loss_aus_den_einstellungen(boerse, speicher):
    # Gekauft zu 101 USDT, Schlusskurs 98 ist 2.97 % tiefer.
    #   Mit dem Standard 3 %: 101 · 97 / 100 = 97.97, 98 liegt darüber: halten.
    #   Mit 2 % aus den Einstellungen: 101 · 98 / 100 = 98.98, 98 liegt darunter: verkaufen.
    position_in_der_datenbank(speicher, preis=101.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(*UNTER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.999)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.999, preis=98.0)
    einstellungen = Einstellungen(n=4, k=1.0, stop_loss_prozent=2.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=einstellungen, jetzt=JETZT)

    assert ergebnis is Signal.VERKAUFEN
