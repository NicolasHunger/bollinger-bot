"""Zeigt, wie der Port in den Tests gemockt wird, und dass der Mock die Schnittstelle einhält.

`create_autospec` baut den Mock nach dem Vorbild von `Boerse`: Er kennt nur die Methoden und
Argumente, die der Port beschreibt. Ein Tippfehler im Methodennamen oder ein fehlendes Argument
fällt so schon im Test auf und nicht erst beim echten `BitgetClient`.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from bollinger_bot.port import Boerse, Kerze, Order


def kerze(schluss):
    return Kerze(
        startzeit=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        eroeffnung=schluss,
        hoch=schluss,
        tief=schluss,
        schluss=schluss,
        volumen=1.0,
    )


@pytest.fixture
def boerse(mocker):
    return mocker.create_autospec(Boerse, instance=True)


def test_mock_liefert_vorgegebene_kerzen(boerse):
    boerse.hole_kerzen.return_value = [kerze(100), kerze(101)]

    kerzen = boerse.hole_kerzen("BTCUSDT", "15min", 2)

    assert [k.schluss for k in kerzen] == [100, 101]
    boerse.hole_kerzen.assert_called_once_with("BTCUSDT", "15min", 2)


def test_mock_liefert_vorgegebene_order(boerse):
    # Für 60 USDT bei 60 000 USDT/BTC: 60 / 60 000 = 0.001 BTC. Bitget zieht 0.1 % Gebühr von
    # den gekauften BTC ab: 0.001 · 0.999 = 0.000999 BTC.
    boerse.kaufe.return_value = Order(order_id="1", menge=0.000999, preis=60_000)

    order = boerse.kaufe("BTCUSDT", 60.0)

    assert order.menge == 0.000999
    assert order.preis == 60_000
    boerse.kaufe.assert_called_once_with("BTCUSDT", 60.0)


def test_mock_kennt_keine_methoden_ausserhalb_des_ports(boerse):
    with pytest.raises(AttributeError):
        boerse.kaufe_alles("BTCUSDT")


def test_mock_lehnt_fehlende_argumente_ab(boerse):
    with pytest.raises(TypeError):
        boerse.kaufe("BTCUSDT")  # betrag fehlt


def test_mock_kauft_mit_betrag_in_usdt(boerse):
    boerse.kaufe(symbol="BTCUSDT", betrag=60.0)

    boerse.kaufe.assert_called_once_with(symbol="BTCUSDT", betrag=60.0)


def test_mock_lehnt_kauf_mit_menge_ab(boerse):
    # Bitget will beim Market-Kauf den Betrag in USDT, darum gilt der alte Name `menge` nicht mehr
    with pytest.raises(TypeError):
        boerse.kaufe(symbol="BTCUSDT", menge=0.001)


def test_mock_verkauft_mit_menge_in_btc(boerse):
    boerse.verkaufe(symbol="BTCUSDT", menge=0.000999)

    boerse.verkaufe.assert_called_once_with(symbol="BTCUSDT", menge=0.000999)


def test_mock_lehnt_verkauf_mit_betrag_ab(boerse):
    with pytest.raises(TypeError):
        boerse.verkaufe(symbol="BTCUSDT", betrag=60.0)


def test_kerze_ist_unveraenderlich():
    with pytest.raises(FrozenInstanceError):
        kerze(100).schluss = 50  # type: ignore[misc]
