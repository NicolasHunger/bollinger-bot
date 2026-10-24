"""Unit-Tests für den Backtest: Kerzen aus der CSV lesen und die simulierte Börse.

Den ganzen Backtest mit echter SQLite-Datei testen die Integrationstests in
`tests/integration/test_backtest.py`.
"""

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from bollinger_bot.backtest import AufgezeichneteBoerse, lies_kerzen
from bollinger_bot.port import Boerse, Kerze, Order

CSV_KOPF = "startzeit,eroeffnung,hoch,tief,schluss,volumen\n"
MIN_15 = timedelta(minutes=15)
ZWOELF_UHR = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def kerzen(*schlusskurse):
    """15-Minuten-Kerzen ab 12:00 UTC mit diesen Schlusskursen."""
    return [
        Kerze(
            startzeit=ZWOELF_UHR + i * MIN_15,
            eroeffnung=schluss,
            hoch=schluss,
            tief=schluss,
            schluss=schluss,
            volumen=1.0,
        )
        for i, schluss in enumerate(schlusskurse)
    ]


def test_lies_kerzen_liest_zeit_mit_utc_und_zahlen_als_float(tmp_path):
    # Zwei Zeilen im Format von scripts/kerzen_holen.py. Bitget lässt Nullen am Ende weg
    # («84927» statt «84927.00»), auch das muss als float ankommen.
    pfad = tmp_path / "kerzen.csv"
    pfad.write_text(
        CSV_KOPF
        + "2026-10-03T18:45:00+00:00,84940.34,84950.41,84927,84940.01,1.402479\n"
        + "2026-10-03T19:00:00+00:00,84940.01,84940.01,84848,84868,14.990596\n"
    )

    kerzen = lies_kerzen(pfad)

    assert kerzen == [
        Kerze(
            startzeit=datetime(2026, 10, 3, 18, 45, tzinfo=UTC),
            eroeffnung=84940.34,
            hoch=84950.41,
            tief=84927.0,
            schluss=84940.01,
            volumen=1.402479,
        ),
        Kerze(
            startzeit=datetime(2026, 10, 3, 19, 0, tzinfo=UTC),
            eroeffnung=84940.01,
            hoch=84940.01,
            tief=84848.0,
            schluss=84868.0,
            volumen=14.990596,
        ),
    ]
    assert all(isinstance(k.tief, float) for k in kerzen)
    assert kerzen[0].startzeit.utcoffset().total_seconds() == 0


def test_lies_kerzen_liefert_die_aelteste_zuerst(tmp_path):
    # Bitget liefert history-candles neueste zuerst. Steht die CSV so, muss lies_kerzen() sie
    # trotzdem älteste zuerst liefern, wie hole_kerzen() im Port: tick() wertet kerzen[-1] aus.
    pfad = tmp_path / "kerzen.csv"
    pfad.write_text(
        CSV_KOPF
        + "2026-10-03T19:00:00+00:00,2,2,2,2,1\n"
        + "2026-10-03T18:45:00+00:00,1,1,1,1,1\n"
    )

    kerzen = lies_kerzen(pfad)

    # Die Kerze ab 18:45 (Schlusskurs 1) kommt vor der ab 19:00 (Schlusskurs 2).
    assert [k.schluss for k in kerzen] == [1.0, 2.0]


def test_lies_kerzen_rechnet_andere_zeitzonen_in_utc_um(tmp_path):
    # 20:45 Schweizer Sommerzeit (+02:00) ist 18:45 UTC. Intern rechnen wir nur in UTC.
    pfad = tmp_path / "kerzen.csv"
    pfad.write_text(CSV_KOPF + "2026-10-03T20:45:00+02:00,1,1,1,1,1\n")

    kerze = lies_kerzen(pfad)[0]

    assert kerze.startzeit == datetime(2026, 10, 3, 18, 45, tzinfo=UTC)
    # == vergleicht nur den Zeitpunkt, darum die Zeitzone extra prüfen.
    assert kerze.startzeit.tzinfo == UTC


def test_lies_kerzen_lehnt_zeiten_ohne_zeitzone_ab(tmp_path):
    # Ohne Zeitzone ist unklar, ob 18:45 UTC oder Schweizer Zeit gemeint ist. Die Meldung nennt
    # die Zeile: Zeile 1 ist die Kopfzeile, die zweite Kerze steht in Zeile 3.
    pfad = tmp_path / "kerzen.csv"
    pfad.write_text(
        CSV_KOPF
        + "2026-10-03T18:45:00+00:00,1,1,1,1,1\n"
        + "2026-10-03T19:00:00,2,2,2,2,1\n"
    )

    with pytest.raises(ValueError, match="Zeile 3"):
        lies_kerzen(pfad)


def test_hole_kerzen_liefert_nur_kerzen_die_bis_jetzt_abgeschlossen_sind():
    # Kerzen ab 12:00, 12:15, 12:30 und 12:45. Um 12:45 ist die Kerze ab 12:30 gerade fertig
    # (Ende 12:30 + 15 min = 12:45), die ab 12:45 läuft noch.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(1, 2, 3, 4), intervall_dauer=MIN_15)

    # Bevor die Backtest-Schleife die Zeit setzt, ist noch keine Kerze abgeschlossen.
    assert boerse.hole_kerzen("BTCUSDT", "15min", 2) == []
    boerse.jetzt = ZWOELF_UHR + 3 * MIN_15
    assert [k.schluss for k in boerse.hole_kerzen("BTCUSDT", "15min", 2)] == [2.0, 3.0]
    # Eine Sekunde früher läuft die Kerze ab 12:30 noch.
    boerse.jetzt = ZWOELF_UHR + 3 * MIN_15 - timedelta(seconds=1)
    assert [k.schluss for k in boerse.hole_kerzen("BTCUSDT", "15min", 2)] == [1.0, 2.0]
    # Mehr verlangt als abgeschlossen: alle abgeschlossenen, wie Bitget kurz nach dem Start.
    assert [k.schluss for k in boerse.hole_kerzen("BTCUSDT", "15min", 20)] == [1.0, 2.0]


@pytest.mark.parametrize("anzahl", [0, -1])
def test_hole_kerzen_lehnt_eine_anzahl_unter_1_ab(anzahl):
    # Wie der Bitget-Client. Ohne Prüfung gäbe `liste[-0:]` bei anzahl 0 alle Kerzen zurück.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(1, 2), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR + 2 * MIN_15

    with pytest.raises(ValueError, match="anzahl"):
        boerse.hole_kerzen("BTCUSDT", "15min", anzahl)


def test_kontostand_am_anfang():
    boerse = AufgezeichneteBoerse(kerzen=kerzen(1), intervall_dauer=MIN_15)
    assert boerse.hole_kontostand("USDT") == 1000.0
    assert boerse.hole_kontostand("BTC") == 0.0
    # Andere Währungen gibt es im simulierten Konto nicht.
    assert boerse.hole_kontostand("ETH") == 0.0

    boerse = AufgezeichneteBoerse(kerzen=kerzen(1), intervall_dauer=MIN_15, startguthaben=250.0)
    assert boerse.hole_kontostand("USDT") == 250.0


def test_kauf_zum_schlusskurs_der_letzten_abgeschlossenen_kerze():
    # Um 12:30 ist die Kerze ab 12:15 (Schlusskurs 50 000) die letzte abgeschlossene, die ab
    # 12:30 (60 000) läuft noch. Kauf für 100 USDT:
    #   100 / 50 000 = 0.002 BTC gekauft, ausgegeben 0.002 · 50 000 = 100.00 USDT
    #   Gebühr 0.1 % von den gekauften BTC: 0.002 · 0.001 = 0.000002 BTC
    #   auf dem Konto 0.002 − 0.000002 = 0.001998 BTC, USDT 1000 − 100 = 900
    boerse = AufgezeichneteBoerse(kerzen=kerzen(40_000, 50_000, 60_000), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR + 2 * MIN_15

    order = boerse.kaufe("BTCUSDT", 100.0)

    assert order == Order(order_id="sim-1", menge=0.001998, preis=50_000.0)
    assert boerse.hole_kontostand("BTC") == 0.001998
    assert boerse.hole_kontostand("USDT") == 900.0


def test_order_vor_der_ersten_abgeschlossenen_kerze_wird_abgelehnt():
    # Um 12:00 beginnt die erste Kerze erst, es gibt noch keinen Kurs.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(50_000), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR

    with pytest.raises(ValueError, match="keine Kerze abgeschlossen"):
        boerse.kaufe("BTCUSDT", 100.0)


def test_kauf_rundet_die_menge_auf_6_stellen_ab_wie_bitget():
    # Kurs 30 000, Kauf für 100 USDT: 100 / 30 000 = 0.0033333… BTC. Bitget rundet die gekaufte
    # Menge auf 6 Stellen ab und gibt nur aus, was diese Menge kostet (Rauchtest von Valentin):
    #   gekauft 0.003333 BTC, ausgegeben 0.003333 · 30 000 = 99.99 USDT
    #   USDT 1000 − 99.99 = 900.01
    #   Gebühr 0.003333 · 0.001 = 0.000003333 BTC, auf dem Konto 0.003329667 BTC
    # Die Menge ohne Gebühr hat mehr als 6 Stellen, wie bei Bitget.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(30_000), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR + MIN_15

    order = boerse.kaufe("BTCUSDT", 100.0)

    assert order.menge == 0.003329667
    assert boerse.hole_kontostand("BTC") == 0.003329667
    assert boerse.hole_kontostand("USDT") == 900.01


def test_verkauf_zum_selben_kurs_kostet_die_gebuehr():
    # Kauf wie oben: 100 USDT zu 50 000, auf dem Konto 0.001998 BTC und 900 USDT.
    # Verkauf der ganzen Menge zum selben Kurs:
    #   0.001998 · 50 000 = 99.90 USDT, Gebühr 0.1 % von den USDT: 0.0999
    #   gutgeschrieben 99.90 − 0.0999 = 99.8001, USDT 900 + 99.8001 = 999.8001
    # Hin und zurück kostet also rund 0.2 % (1000 − 999.8001 = 0.1999 USDT).
    boerse = AufgezeichneteBoerse(kerzen=kerzen(40_000, 50_000, 60_000), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR + 2 * MIN_15
    boerse.kaufe("BTCUSDT", 100.0)

    order = boerse.verkaufe("BTCUSDT", 0.001998)

    assert order == Order(order_id="sim-2", menge=0.001998, preis=50_000.0)
    assert boerse.hole_kontostand("BTC") == 0.0
    # approx: In float ergibt 900 + 99.8001 genau 999.8000999999999.
    assert boerse.hole_kontostand("USDT") == pytest.approx(999.8001)


def test_kauf_ueber_dem_freien_guthaben_wird_abgelehnt():
    # Bitget lehnt eine Order ab, die mehr kostet, als frei verfügbar ist. Genau auf der Grenze
    # (1000 USDT bei 1000 USDT Guthaben) ist der Kauf erlaubt, einen Rappen mehr nicht. Bei der
    # Ablehnung bleibt das Konto unverändert.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(50_000), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR + MIN_15

    with pytest.raises(ValueError, match="USDT"):
        boerse.kaufe("BTCUSDT", 1000.01)
    assert boerse.hole_kontostand("USDT") == 1000.0
    assert boerse.hole_kontostand("BTC") == 0.0

    # 1000 / 50 000 = 0.02 BTC, ausgegeben 0.02 · 50 000 = 1000 USDT: alles weg.
    boerse.kaufe("BTCUSDT", 1000.0)
    assert boerse.hole_kontostand("USDT") == 0.0



def test_verkauf_ueber_dem_bestand_wird_abgelehnt():
    # Auf dem Konto liegen nach dem Kauf 0.001998 BTC (siehe oben). Mehr lässt sich nicht
    # verkaufen, schon 0.000001 BTC mehr wird abgelehnt.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(50_000), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR + MIN_15
    boerse.kaufe("BTCUSDT", 100.0)

    with pytest.raises(ValueError, match="BTC"):
        boerse.verkaufe("BTCUSDT", 0.001999)
    assert boerse.hole_kontostand("BTC") == 0.001998
    assert boerse.hole_kontostand("USDT") == 900.0


@pytest.mark.parametrize(
    ("methode", "wert"),
    [("kaufe", 0.0), ("kaufe", -100.0), ("verkaufe", 0.0), ("verkaufe", -0.001)],
)
def test_order_ohne_positiven_betrag_wird_abgelehnt(methode, wert):
    # Wie der Bitget-Client. Ohne Prüfung schrieb kaufe(-100) 100 USDT gut, und der BTC-Bestand
    # wurde negativ. Das Konto bleibt unverändert.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(50_000), intervall_dauer=MIN_15)
    boerse.jetzt = ZWOELF_UHR + MIN_15

    with pytest.raises(ValueError, match="grösser als 0"):
        getattr(boerse, methode)("BTCUSDT", wert)
    assert boerse.hole_kontostand("USDT") == 1000.0
    assert boerse.hole_kontostand("BTC") == 0.0


@pytest.mark.parametrize("methode", ["hole_kerzen", "hole_kontostand", "kaufe", "verkaufe"])
def test_simulierte_boerse_hat_dieselben_methoden_wie_der_port(methode):
    # tick() ruft die Börse mit Positionsargumenten auf. Stimmen Namen oder Reihenfolge der
    # Parameter nicht mit dem Port überein, verhält sich der Backtest anders als der Bot.
    erwartet = inspect.signature(getattr(Boerse, methode))
    tatsaechlich = inspect.signature(getattr(AufgezeichneteBoerse, methode))
    assert list(tatsaechlich.parameters) == list(erwartet.parameters)
