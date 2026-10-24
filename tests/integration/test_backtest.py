"""Integrationstests für den Backtest: der echte tick() mit simulierter Börse und SQLite-Datei.

Die kleinen Tests rechnen wie `tests/integration/test_tick.py` mit Kursen um 100 USDT, n = 4
Kerzen und k = 1, damit sich alles im Kopf nachrechnen lässt.
"""

from datetime import UTC, datetime, timedelta

import pytest

from bollinger_bot.backtest import Ergebnis, backtest
from bollinger_bot.port import Kerze
from bollinger_bot.speicher import Speicher, Trade
from bollinger_bot.tick import Einstellungen

MIN_15 = timedelta(minutes=15)
ZWOELF_UHR = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
EINSTELLUNGEN = Einstellungen(n=4, k=1.0)


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
