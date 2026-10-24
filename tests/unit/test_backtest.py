"""Unit-Tests für den Backtest: Kerzen aus der CSV lesen und die simulierte Börse.

Den ganzen Backtest mit echter SQLite-Datei testen die Integrationstests in
`tests/integration/test_backtest.py`.
"""

from datetime import UTC, datetime, timedelta

from bollinger_bot.backtest import AufgezeichneteBoerse, lies_kerzen
from bollinger_bot.port import Kerze, Order

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


def test_hole_kerzen_liefert_nur_kerzen_die_bis_jetzt_abgeschlossen_sind():
    # Kerzen ab 12:00, 12:15, 12:30 und 12:45. Um 12:45 ist die Kerze ab 12:30 gerade fertig
    # (Ende 12:30 + 15 min = 12:45), die ab 12:45 läuft noch.
    boerse = AufgezeichneteBoerse(kerzen=kerzen(1, 2, 3, 4), intervall_dauer=MIN_15)

    boerse.jetzt = ZWOELF_UHR + 3 * MIN_15
    assert [k.schluss for k in boerse.hole_kerzen("BTCUSDT", "15min", 2)] == [2.0, 3.0]
    # Eine Sekunde früher läuft die Kerze ab 12:30 noch.
    boerse.jetzt = ZWOELF_UHR + 3 * MIN_15 - timedelta(seconds=1)
    assert [k.schluss for k in boerse.hole_kerzen("BTCUSDT", "15min", 2)] == [1.0, 2.0]
    # Mehr verlangt als abgeschlossen: alle abgeschlossenen, wie Bitget kurz nach dem Start.
    assert [k.schluss for k in boerse.hole_kerzen("BTCUSDT", "15min", 20)] == [1.0, 2.0]


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
