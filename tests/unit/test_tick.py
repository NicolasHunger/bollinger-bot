"""Unit-Tests für die Einstellungen von `tick()`.

Den Ablauf von `tick()` selbst testen die Integrationstests in `tests/integration/test_tick.py`,
weil er eine echte SQLite-Datei braucht.
"""

from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from bollinger_bot.risiko import BTCUSDT_SPOT
from bollinger_bot.tick import Einstellungen


def test_standardwerte_der_einstellungen():
    einstellungen = Einstellungen()

    assert einstellungen.symbol == "BTCUSDT"
    assert einstellungen.basiswaehrung == "BTC"
    assert einstellungen.quotewaehrung == "USDT"
    assert einstellungen.intervall == "15min"
    assert einstellungen.intervall_dauer == timedelta(minutes=15)
    assert einstellungen.n == 20
    assert einstellungen.k == 2.0
    assert einstellungen.anteil_prozent == 10.0
    assert einstellungen.stop_loss_prozent == 3.0
    assert einstellungen.regeln == BTCUSDT_SPOT


def test_einstellungen_sind_unveraenderlich():
    with pytest.raises(FrozenInstanceError):
        Einstellungen().n = 5  # type: ignore[misc]
