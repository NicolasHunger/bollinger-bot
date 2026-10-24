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
from bollinger_bot.risiko import Handelsregeln
from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade
from bollinger_bot.tick import Einstellungen, tick

MIN_1 = timedelta(minutes=1)
MIN_5 = timedelta(minutes=5)
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


def kerzen(*schlusskurse, letzte=LETZTE_KERZE, abstand=MIN_15):
    """Kerzen im Abstand `abstand` mit diesen Schlusskursen, die letzte beginnt um `letzte`.

    Eröffnung, Hoch und Tief liegen um den Schlusskurs herum und sind alle verschieden, damit
    auffällt, wenn tick() zwei davon vertauscht.
    """
    anzahl = len(schlusskurse)
    return [
        Kerze(
            startzeit=letzte - (anzahl - 1 - i) * abstand,
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


def bitget_fuehrt_kauf_aus(boerse, konto):
    """`kaufe()` bucht das Konto um wie Bitget, für einen Kauf von 100 USDT.

    100 USDT zu 100 USDT/BTC ergeben 1 BTC, Bitget zieht 0.1 % Gebühr ab: 0.999 BTC.
    """

    def kauf(symbol, betrag):
        konto["USDT"] -= betrag
        konto["BTC"] += 0.999
        return Order(order_id="kauf-1", menge=0.999, preis=100.0)

    boerse.kaufe.side_effect = kauf


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


def test_ohne_kerzen_von_bitget_wird_im_naechsten_durchgang_neu_versucht(
    boerse, speicher, caplog
):
    # Liefert Bitget gar keine Kerze, ist es wie eine Kerze, die noch fehlt: nichts speichern,
    # eine Minute später neu versuchen (statt eines IndexError ohne verständliche Meldung).
    caplog.set_level(logging.INFO)
    boerse.hole_kerzen.return_value = []

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is None
    assert speicher.hole_signale() == []
    assert speicher.hole_kontostaende() == []
    assert "noch nicht" in caplog.text


def test_liefert_bitget_schon_eine_neuere_kerze_wird_sie_ausgewertet(boerse, speicher):
    # Die Uhr der VM geht nach: Um 12:29:59 erwartet tick() die Kerze ab 12:00. Bitget hat nach
    # seiner Uhr die Kerze ab 12:15 schon abgeschlossen und liefert sie als neueste. Ausgewertet
    # wird die neueste (Bänder wie im Test für «halten»: 98 / 100 / 102, Schlusskurs 102).
    boerse.hole_kerzen.return_value = kerzen(98, 102, 98, 102, letzte=LETZTE_KERZE + MIN_15)
    guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0)
    jetzt = datetime(2026, 10, 24, 12, 29, 59, tzinfo=UTC)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=jetzt)

    assert ergebnis is Signal.HALTEN
    assert speicher.letzte_signal_startzeit() == LETZTE_KERZE + MIN_15


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
    bitget_fuehrt_kauf_aus(boerse, guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0))

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
    # Eine gültige Order, damit ein fälschlicher Kauf an assert_not_called() scheitert.
    boerse.kaufe.return_value = Order(order_id="kauf-1", menge=0.999, preis=100.0)

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
    boerse.kaufe.return_value = Order(order_id="kauf-2", menge=0.999, preis=100.0)

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
    # Gekauft zu 97 USDT, bei Bitget liegen genau die gekauften 0.999 BTC.
    kauf = position_in_der_datenbank(speicher, preis=97.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(*UEBER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.999)
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


def test_verkaufen_verkauft_auch_reste_aus_frueheren_abrundungen(boerse, speicher):
    # Bitget meldet die gekaufte Menge ohne Gebühr mit mehr als 6 Stellen (0.9985678 BTC), Bitget
    # nimmt beim Verkauf aber nur 6 an. Ein früherer Verkauf hat 0.0000009 BTC liegen lassen:
    #   bei Bitget 0.9985678 + 0.0000009 = 0.9985687 BTC
    # Verkauft wird alles, was bei Bitget liegt, abgerundet: 0.998568 BTC. Liegen bleiben nur
    # 0.0000007 BTC, also nie mehr als eine Stelle. Mit nur der Position (0.998567) sammelten
    # sich die Reste an, bis das Sicherheitsnetz beim Kaufen sie für eine Position hält.
    position_in_der_datenbank(speicher, preis=97.0, menge=0.9985678)
    boerse.hole_kerzen.return_value = kerzen(*UEBER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.9985687)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.998568, preis=102.0)

    tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    boerse.verkaufe.assert_called_once_with("BTCUSDT", 0.998568)


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


def test_schlusskurs_genau_auf_dem_unteren_band_kauft_nicht(boerse, speicher):
    # Keine Position. Bänder 98 / 100 / 102 (siehe UNTER_DEM_MITTELBAND), Schlusskurs 98 liegt
    # genau auf dem unteren Band: 98 < 98 ist falsch, also halten. Hält auch fest, dass tick()
    # das untere Band und nicht das Mittelband als `unteres_band` übergibt (98 < 100 wäre wahr).
    boerse.hole_kerzen.return_value = kerzen(*UNTER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0)
    boerse.kaufe.return_value = Order(order_id="kauf-1", menge=0.999, preis=98.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.HALTEN
    boerse.kaufe.assert_not_called()


def test_mit_position_zwischen_unterem_band_und_mittelband_wird_gehalten(boerse, speicher):
    # Schlusskurse 105, 99, 97, 99:
    #   Mittelband = 400 / 4 = 100, Abweichungen +5, −1, −3, −1
    #   Quadrate 25 + 1 + 9 + 1 = 36: Varianz = 36 / 4 = 9, σ = 3
    #   k = 1: unteres Band 97, oberes Band 103
    # Gekauft zu 100, Stop-Loss 100 · 97 / 100 = 97. Schlusskurs 99 liegt nicht über dem
    # Mittelband 100 und nicht unter 97: halten. Würde tick() das untere Band als `mittelband`
    # übergeben, wäre 99 > 97 und der Bot würde verkaufen.
    position_in_der_datenbank(speicher, preis=100.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(105, 99, 97, 99)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.999)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.999, preis=99.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.HALTEN
    boerse.verkaufe.assert_not_called()
    [eintrag] = speicher.hole_signale()
    assert (eintrag.unteres_band, eintrag.mittelband, eintrag.oberes_band) == (97.0, 100.0, 103.0)


def test_verkaufen_ohne_order_wenn_der_wert_unter_dem_mindestbetrag_liegt(
    boerse, speicher, caplog
):
    # Position 0.005 BTC, Wert 0.005 · 102 = 0.51 USDT, unter dem Mindestbetrag von 1 USDT:
    # verkaufsmenge() ergibt 0.0, Bitget würde die Order ablehnen.
    kauf = position_in_der_datenbank(speicher, preis=97.0, menge=0.005)
    boerse.hole_kerzen.return_value = kerzen(*UEBER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.005)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.005, preis=102.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.VERKAUFEN
    boerse.verkaufe.assert_not_called()
    assert speicher.offene_position() == kauf
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert [e.signal for e in speicher.hole_signale()] == ["verkaufen"]


def test_tick_nutzt_alle_einstellungen(boerse, speicher):
    # Ein anderes Handelspaar mit 5-Minuten-Kerzen und anderen Regeln. Würde tick() irgendwo
    # einen Standardwert fest einbauen (BTCUSDT, USDT, 15min, 10 %, 1 USDT), fiele es hier auf.
    einstellungen = Einstellungen(
        symbol="ETHUSDC",
        basiswaehrung="ETH",
        quotewaehrung="USDC",
        intervall="5min",
        intervall_dauer=MIN_5,
        n=4,
        k=1.0,
        anteil_prozent=20.0,
        regeln=Handelsregeln(mengen_stellen=4, betrag_stellen=2, mindestbetrag=5.0),
    )
    # Ausgewertet ist die Kerze ab 12:05. Um 12:16 ist die 5-Minuten-Kerze ab 12:10 fertig (mit
    # 15 Minuten wäre die letzte fertige die ab 12:00, also nichts Neues).
    speicher.speichere_signal(signal_eintrag(LETZTE_KERZE + 5 * MIN_1))
    # Schlusskurse 2120, 2160, 2120, 2000 (KAUFSIGNAL mal 20):
    #   Mittelband = 8400 / 4 = 2100, Abweichungen +20, +60, +20, −100
    #   Quadrate 400 + 3600 + 400 + 10 000 = 14 400: Varianz = 3600, σ = 60
    #   k = 1: unteres Band 2040. Schlusskurs 2000 liegt darunter: kaufen.
    boerse.hole_kerzen.return_value = kerzen(
        2120, 2160, 2120, 2000, letzte=LETZTE_KERZE + 10 * MIN_1, abstand=MIN_5
    )
    # 0.002 ETH · 2000 = 4.00 USDC, unter dem Mindestbetrag von 5 USDC: Staub, kein Hindernis
    # (mit dem Mindestbetrag 1 von BTCUSDT_SPOT wäre es eine unbekannte Position).
    konto = guthaben_bei_bitget(boerse, USDC=1000.0, ETH=0.002)

    def kauf(symbol, betrag):
        # 200 USDC zu 2000 ergeben 0.1 ETH, ohne 0.1 % Gebühr 0.0999 ETH.
        konto["USDC"] -= betrag
        konto["ETH"] += 0.0999
        return Order(order_id="kauf-eth", menge=0.0999, preis=2000.0)

    boerse.kaufe.side_effect = kauf

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=einstellungen, jetzt=JETZT)

    assert ergebnis is Signal.KAUFEN
    boerse.hole_kerzen.assert_called_once_with("ETHUSDC", "5min", 4)
    # Kaufbetrag: 20 % von 1000 USDC = 200.00 USDC
    boerse.kaufe.assert_called_once_with("ETHUSDC", 200.0)
    assert speicher.hole_trades() == [
        Trade(zeit=JETZT, seite="kauf", menge=0.0999, preis=2000.0, order_id="kauf-eth")
    ]
    assert speicher.letzte_signal_startzeit() == LETZTE_KERZE + 10 * MIN_1
    assert speicher.hole_kontostaende() == [Kontostand(zeit=JETZT, waehrung="USDC", betrag=800.0)]


def test_ein_kauf_steht_im_log_auch_wenn_das_speichern_scheitert(
    boerse, speicher, mocker, caplog
):
    # Bitget hat gekauft, die Datei lässt sich aber nicht schreiben. Die Order-ID muss trotzdem
    # im Log stehen, sonst lässt sich die Position nicht von Hand nachtragen.
    caplog.set_level(logging.INFO)
    boerse.hole_kerzen.return_value = kerzen(*KAUFSIGNAL)
    bitget_fuehrt_kauf_aus(boerse, guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0))
    mocker.patch.object(speicher, "speichere_trade", side_effect=OSError("disk I/O error"))

    with pytest.raises(OSError):
        tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert "kauf-1" in caplog.text


def test_ein_verkauf_steht_im_log_auch_wenn_das_speichern_scheitert(
    boerse, speicher, mocker, caplog
):
    caplog.set_level(logging.INFO)
    position_in_der_datenbank(speicher, preis=97.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(*UEBER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=900.0, BTC=0.999)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.999, preis=102.0)
    mocker.patch.object(speicher, "speichere_trade", side_effect=OSError("disk I/O error"))

    with pytest.raises(OSError):
        tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert "verkauf-1" in caplog.text


def test_fehler_im_log_wenn_bitget_die_position_nicht_mehr_hat(boerse, speicher, caplog):
    # Die Datenbank hat eine offene Position (0.999 · 102 = 101.90 USDT, über dem Mindestbetrag),
    # bei Bitget liegen aber keine BTC: Ein früherer Verkauf ist ausgeführt, aber nicht
    # gespeichert worden (Absturz zwischen verkaufe() und speichere_trade()). Das ist kein Staub,
    # sondern ein Fehler, den man von Hand nachtragen muss: ERROR mit der Order-ID des Kaufs.
    kauf = position_in_der_datenbank(speicher, preis=97.0, menge=0.999)
    boerse.hole_kerzen.return_value = kerzen(*UEBER_DEM_MITTELBAND)
    guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0)
    boerse.verkaufe.return_value = Order(order_id="verkauf-1", menge=0.999, preis=102.0)

    ergebnis = tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert ergebnis is Signal.VERKAUFEN
    boerse.verkaufe.assert_not_called()
    assert speicher.offene_position() == kauf
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert "kauf-1" in caplog.text


class AbsturzBeimErstenSignal(Speicher):
    """Speicher, der beim ersten `speichere_signal()` abstürzt, wie bei einem Stromausfall."""

    def __init__(self, pfad):
        super().__init__(pfad)
        self.abgestuerzt = False

    def speichere_signal(self, eintrag):
        if not self.abgestuerzt:
            self.abgestuerzt = True
            raise RuntimeError("Absturz")
        return super().speichere_signal(eintrag)


def test_nach_absturz_zwischen_kauf_und_signal_wird_nicht_doppelt_gekauft(boerse, tmp_path):
    boerse.hole_kerzen.return_value = kerzen(*KAUFSIGNAL)
    bitget_fuehrt_kauf_aus(boerse, guthaben_bei_bitget(boerse, USDT=1000.0, BTC=0.0))

    with AbsturzBeimErstenSignal(tmp_path / "bot.db") as speicher:
        # Erster Durchgang: Kauf und Trade gespeichert, dann Absturz vor dem Signal.
        with pytest.raises(RuntimeError, match="Absturz"):
            tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)
        assert speicher.hole_signale() == []

        # Eine Minute später: Ohne Signal gilt die Kerze ab 12:00 als nicht ausgewertet. Die
        # Position steht aber schon in der Datenbank (Kaufpreis 100), darum kein zweiter Kauf:
        # Schlusskurs 100 liegt nicht über dem Mittelband 105 und nicht unter dem Stop-Loss
        # 100 · 97 / 100 = 97, also halten.
        ergebnis = tick(
            boerse=boerse,
            speicher=speicher,
            einstellungen=EINSTELLUNGEN,
            jetzt=JETZT + timedelta(minutes=1),
        )

        assert ergebnis is Signal.HALTEN
        boerse.kaufe.assert_called_once()
        assert [t.order_id for t in speicher.hole_trades()] == ["kauf-1"]
        assert [e.signal for e in speicher.hole_signale()] == ["halten"]
        # Nur der zweite Durchgang hat eine Kontostand-Zeile: 1000 − 100 = 900 USDT.
        assert speicher.hole_kontostaende() == [
            Kontostand(zeit=JETZT + timedelta(minutes=1), waehrung="USDT", betrag=900.0)
        ]


def test_fehler_der_boerse_kommt_heraus_und_nichts_wird_gespeichert(boerse, speicher):
    # tick() fängt Fehler der Börse nicht ab: Die Polling-Schleife protokolliert sie und
    # versucht es im nächsten Durchgang neu. Der echte Client wirft hier einen BitgetFehler.
    boerse.hole_kerzen.side_effect = ConnectionError("Bitget nicht erreichbar")

    with pytest.raises(ConnectionError, match="Bitget nicht erreichbar"):
        tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert speicher.hole_signale() == []
    assert speicher.hole_trades() == []
    assert speicher.hole_kontostaende() == []


def test_scheitert_der_kontostand_wird_die_kerze_neu_ausgewertet(boerse, speicher):
    # Signal «halten» (wie im Test dafür), aber Bitget antwortet beim Kontostand nicht. Würde
    # das Signal trotzdem gespeichert, gälte die Kerze als ausgewertet, und ihre Kontostand-Zeile
    # fehlte für immer. Darum darf in dem Fall gar nichts gespeichert sein.
    boerse.hole_kerzen.return_value = kerzen(98, 102, 98, 102)
    boerse.hole_kontostand.side_effect = ConnectionError("Bitget nicht erreichbar")

    with pytest.raises(ConnectionError, match="Bitget nicht erreichbar"):
        tick(boerse=boerse, speicher=speicher, einstellungen=EINSTELLUNGEN, jetzt=JETZT)

    assert speicher.hole_signale() == []
    assert speicher.hole_kontostaende() == []
