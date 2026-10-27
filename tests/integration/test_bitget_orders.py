"""Integrationstests: Kontostand, Kauf und Verkauf über den BitgetClient, Bitget mit respx gemockt.

Eine Market-Order läuft bei Bitget in zwei Schritten:
1. `POST /api/v2/spot/trade/place-order` gibt nur die Order-ID zurück.
2. `GET /api/v2/spot/trade/orderInfo` liefert Status, ausgeführte Menge, Preis und Gebühr.

Die Antworten haben die Form aus der Bitget-Doku (Classic Spot, «Get Order Info»). Das Format
der Gebühr (`feeDetail`) ist mit einer echten Demo-Order geprüft, siehe
`test_kauf_mit_echter_antwort_aus_dem_rauchtest`.
"""

import json
import logging

import httpx
import pytest
import respx

from bollinger_bot.bitget_client import BitgetClient, BitgetFehler, OrderFehler
from bollinger_bot.port import Order

BASIS = "https://api.bitget.com"
ASSETS_URL = f"{BASIS}/api/v2/spot/account/assets"
ORDER_URL = f"{BASIS}/api/v2/spot/trade/place-order"
INFO_URL = f"{BASIS}/api/v2/spot/trade/orderInfo"


def erfolg(data):
    return {"code": "00000", "msg": "success", "requestTime": 1790773421524, "data": data}


def order_info(*, status="filled", base_volume="0.001176", preis="85000", gebuehr_r=-0.000001176):
    # Kauf für 100 USDT zu 85 000 USDT/BTC: 100 / 85 000 = 0.00117647 BTC, Bitget füllt 0.001176.
    # Gebühr 0.1 % von 0.001176 BTC = 0.000001176 BTC, abgezogen von den gekauften BTC.
    return {
        "orderId": "1001",
        "symbol": "BTCUSDT",
        "status": status,
        "priceAvg": preis,
        "baseVolume": base_volume,
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "feeDetail": json.dumps({"newFees": {"c": 0, "d": 0, "r": gebuehr_r, "t": gebuehr_r}}),
    }


class FakeSchlaf:
    """Merkt sich die Pausen, statt echt zu warten."""

    def __init__(self):
        self.pausen = []

    def __call__(self, sekunden):
        self.pausen.append(sekunden)


@pytest.fixture
def schlaf():
    return FakeSchlaf()


@pytest.fixture
def client(schlaf):
    client = BitgetClient(
        api_key="fake-key", api_secret="fake-secret", passphrase="fake-pass", schlafen=schlaf
    )
    yield client
    client.schliessen()


# --- Kontostand ---


@respx.mock
def test_kontostand_ist_das_frei_verfuegbare_guthaben(client):
    route = respx.get(ASSETS_URL).respond(
        json=erfolg([{"coin": "USDT", "available": "1000.5", "frozen": "20", "locked": "0"}])
    )

    assert client.hole_kontostand("USDT") == 1000.5
    anfrage = route.calls.last.request
    assert anfrage.url.params["coin"] == "USDT"
    assert "ACCESS-SIGN" in anfrage.headers  # Kontodaten sind privat, also signiert


@respx.mock
def test_kontostand_findet_die_waehrung_auch_kleingeschrieben(client):
    # Im Beispiel der Bitget-Doku steht "usdt"
    respx.get(ASSETS_URL).respond(json=erfolg([{"coin": "usdt", "available": "42"}]))

    assert client.hole_kontostand("USDT") == 42.0


@respx.mock
@pytest.mark.parametrize("data", [[], None, [{"coin": "BTC", "available": "0.5"}]])
def test_kontostand_ohne_eintrag_ist_null(client, data):
    respx.get(ASSETS_URL).respond(json=erfolg(data))

    assert client.hole_kontostand("USDT") == 0.0


# --- Kaufen ---


@respx.mock
def test_kauf_schickt_market_order_mit_betrag_in_usdt(client):
    order = respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001", "clientOid": "x"}))
    respx.get(INFO_URL).respond(json=erfolg([order_info()]))

    client.kaufe("BTCUSDT", 100.0)

    assert json.loads(order.calls.last.request.content) == {
        "symbol": "BTCUSDT",
        "side": "buy",
        "orderType": "market",
        "force": "gtc",
        "size": "100.0",  # bei Market-Kauf: Betrag in USDT
    }


@respx.mock
def test_kauf_liefert_menge_ohne_gebuehr_und_durchschnittspreis(client):
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    info = respx.get(INFO_URL).respond(json=erfolg([order_info()]))

    ergebnis = client.kaufe("BTCUSDT", 100.0)

    # 0.001176 − 0.000001176 = 0.001174824 BTC liegen danach auf dem Konto
    assert ergebnis == Order(order_id="1001", menge=pytest.approx(0.001174824), preis=85000.0)
    assert info.calls.last.request.url.params["orderId"] == "1001"


@respx.mock
def test_kauf_mit_echter_antwort_aus_dem_rauchtest(client):
    # Antwort der Demo-API vom 21.10.2026 auf einen Kauf für 5 USDT, gekürzt auf die Felder, die
    # der Client liest. Gebühr 0.1 %: 0.000059 · 0.001 = 0.000000059 BTC. Bitget meldete danach
    # genau 0.000058941 BTC frei (0.000059 − 0.000000059).
    echt = {
        "orderId": "1496770255489114112",
        "status": "filled",
        "baseVolume": "0.000059",
        "quoteVolume": "4.9227192800000000",
        "priceAvg": "83435.9200000000000000",
        "baseCoin": "BTC",
        "feeDetail": (
            '{"BTC":{"deduction":false,"feeCoinCode":"BTC","totalDeductionFee":0,'
            '"totalFee":-5.90000000E-8},"newFees":{"c":0,"d":0,"deduction":false,'
            '"r":-5.9E-8,"t":-5.9E-8,"totalDeductionFee":0}}'
        ),
    }
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1496770255489114112"}))
    respx.get(INFO_URL).respond(json=erfolg([echt]))

    ergebnis = client.kaufe("BTCUSDT", 5.0)

    assert ergebnis.menge == pytest.approx(0.000058941)
    assert ergebnis.preis == 83435.92


@respx.mock
def test_kauf_mit_bgb_rabatt_hat_keinen_abzug(client):
    # Wer die Gebühr in BGB bezahlt, bekommt die ganze Menge: r ist dann 0
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    respx.get(INFO_URL).respond(json=erfolg([order_info(gebuehr_r=0)]))

    assert client.kaufe("BTCUSDT", 100.0).menge == 0.001176


@respx.mock
def test_kauf_wartet_bis_die_order_ausgefuehrt_ist(client, schlaf):
    # Erste Abfrage: Order noch nicht sichtbar, zweite: läuft noch, dritte: ausgeführt
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    info = respx.get(INFO_URL)
    info.side_effect = [
        respx.MockResponse(json=erfolg([])),
        respx.MockResponse(json=erfolg([order_info(status="live", base_volume="0")])),
        respx.MockResponse(json=erfolg([order_info()])),
    ]

    ergebnis = client.kaufe("BTCUSDT", 100.0)

    assert ergebnis.preis == 85000.0
    assert info.call_count == 3
    assert schlaf.pausen == [0.5, 0.5]  # zwischen den drei Abfragen, nicht danach


@respx.mock
def test_kauf_gibt_nach_10_abfragen_auf(client, schlaf):
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    info = respx.get(INFO_URL).respond(json=erfolg([order_info(status="live", base_volume="0")]))

    with pytest.raises(OrderFehler, match="Order 1001 nach 10 Abfragen nicht bestätigt") as f:
        client.kaufe("BTCUSDT", 100.0)

    assert f.value.order_id == "1001"
    assert info.call_count == 10
    assert len(schlaf.pausen) == 9


@respx.mock
def test_einzelner_netzfehler_beim_abfragen_wird_ueberbrueckt(client, schlaf, caplog):
    # Die Order ist schon platziert. Fällt die erste Abfrage aus, muss der Client weiter fragen,
    # sonst wüsste tick() nichts vom Kauf und kaufte eine Minute später noch einmal.
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    info = respx.get(INFO_URL)
    info.side_effect = [
        httpx.ConnectTimeout("Zeitüberschreitung"),
        respx.MockResponse(json=erfolg([order_info()])),
    ]

    with caplog.at_level(logging.WARNING):
        ergebnis = client.kaufe("BTCUSDT", 100.0)

    assert ergebnis.order_id == "1001"
    assert info.call_count == 2
    assert schlaf.pausen == [0.5]
    assert "Abfrage 1 von Order 1001 fehlgeschlagen" in caplog.text


@respx.mock
def test_zu_viele_anfragen_beim_abfragen_werden_ueberbrueckt(client):
    # HTTP 429: Bitget bremst, weil zu viele Anfragen kamen. Beim nächsten Versuch geht es wieder.
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    info = respx.get(INFO_URL)
    info.side_effect = [
        respx.MockResponse(429, json={"code": "429", "msg": "Too Many Requests"}),
        respx.MockResponse(json=erfolg([order_info()])),
    ]

    assert client.kaufe("BTCUSDT", 100.0).preis == 85000.0


@respx.mock
def test_dauernder_netzfehler_nennt_order_id_und_ursache(client, schlaf):
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    info = respx.get(INFO_URL).mock(side_effect=httpx.ConnectTimeout("Zeitüberschreitung"))

    with pytest.raises(OrderFehler, match="zuletzt: Bitget nicht erreichbar") as f:
        client.kaufe("BTCUSDT", 100.0)

    assert f.value.order_id == "1001"
    assert isinstance(f.value, BitgetFehler)  # die Polling-Schleife fängt ihn wie jeden anderen
    assert info.call_count == 10


@respx.mock
def test_teilweise_ausgefuehrt_ist_ein_zwischenstand(client, schlaf):
    # "partially_filled" heisst: Die Order läuft noch. Erst "filled" ist das Ergebnis.
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    info = respx.get(INFO_URL)
    info.side_effect = [
        respx.MockResponse(
            json=erfolg([order_info(status="partially_filled", base_volume="0.0005")])
        ),
        respx.MockResponse(json=erfolg([order_info()])),
    ]

    ergebnis = client.kaufe("BTCUSDT", 100.0)

    # die ganze Menge aus "filled": 0.001176 − 0.000001176 = 0.001174824
    assert ergebnis.menge == pytest.approx(0.001174824)
    assert info.call_count == 2


@respx.mock
def test_ausgefuehrte_order_ohne_preis_nennt_order_id(client):
    # Bitget hat ausgeführt, aber die Antwort ist unvollständig: Das muss tick() erfahren.
    ohne_preis = {k: v for k, v in order_info().items() if k != "priceAvg"}
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    respx.get(INFO_URL).respond(json=erfolg([ohne_preis]))

    with pytest.raises(OrderFehler, match="Menge oder Preis nicht lesbar") as f:
        client.kaufe("BTCUSDT", 100.0)

    assert f.value.order_id == "1001"


@respx.mock
def test_teilweise_ausgefuehrte_order_liefert_die_ausgefuehrte_menge(client):
    # Im dünnen Demo-Markt kann eine Market-Order nur teilweise ausgeführt werden. Bitget setzt
    # sie dann auf "cancelled". Gekauft ist trotzdem, was ausgeführt wurde.
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    respx.get(INFO_URL).respond(
        json=erfolg([order_info(status="cancelled", base_volume="0.0005", gebuehr_r=-0.000001)])
    )

    # 0.0005 − 0.000001 = 0.000499
    assert client.kaufe("BTCUSDT", 100.0).menge == pytest.approx(0.000499)


@respx.mock
def test_abgebrochene_order_ohne_ausfuehrung_ist_ein_fehler(client):
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    respx.get(INFO_URL).respond(
        json=erfolg([order_info(status="cancelled", base_volume="0", gebuehr_r=0)])
    )

    with pytest.raises(OrderFehler, match="Order 1001 wurde nicht ausgeführt") as f:
        client.kaufe("BTCUSDT", 100.0)

    assert f.value.order_id == "1001"


@respx.mock
def test_abgelehnte_order_wird_als_bitget_fehler_geworfen(client):
    # Echte Antwort der Demo-API bei leerem Spot-Konto (Rauchtest vom 21.10.2026), HTTP 400
    respx.post(ORDER_URL).respond(400, json={"code": "43012", "msg": "Insufficient balance"})
    info = respx.get(INFO_URL)

    with pytest.raises(BitgetFehler, match="Insufficient balance"):
        client.kaufe("BTCUSDT", 100.0)

    assert not info.called  # nach einer Ablehnung gibt es nichts abzufragen


@respx.mock
def test_unlesbare_gebuehr_laesst_den_kauf_nicht_scheitern(client, caplog):
    # Die Order ist schon ausgeführt: Ein Fehler beim Lesen der Gebühr darf den Trade nicht
    # verschwinden lassen, sonst wüsste tick() nichts von der offenen Position.
    kaputt = {**order_info(), "feeDetail": "kein JSON"}
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    respx.get(INFO_URL).respond(json=erfolg([kaputt]))

    with caplog.at_level(logging.WARNING):
        ergebnis = client.kaufe("BTCUSDT", 100.0)

    # Ersatz: die bekannten 0.1 %, 0.001176 − 0.000001176 = 0.001174824. Ohne Abzug meldete der
    # Client mehr, als auf dem Konto liegt, und jeder Verkauf scheiterte.
    assert ergebnis.menge == pytest.approx(0.001174824)
    assert "Gebühr nicht lesbar" in caplog.text


@respx.mock
def test_unplausible_gebuehr_wird_durch_die_bekannten_0_1_prozent_ersetzt(client, caplog):
    # Grenzwert 1 %: 0.00001176 BTC sind genau 1 % von 0.001176, also schon unplausibel.
    # Ersatz: 0.1 %, 0.001176 − 0.000001176 = 0.001174824
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    respx.get(INFO_URL).respond(json=erfolg([order_info(gebuehr_r=-0.00001176)]))

    with caplog.at_level(logging.WARNING):
        ergebnis = client.kaufe("BTCUSDT", 100.0)

    assert ergebnis.menge == pytest.approx(0.001174824)
    assert "unplausibel" in caplog.text


@respx.mock
def test_gebuehr_knapp_unter_1_prozent_wird_abgezogen(client):
    # Grenzwert: 0.0000117 BTC liegt knapp unter 1 % von 0.001176 (0.00001176)
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "1001"}))
    respx.get(INFO_URL).respond(json=erfolg([order_info(gebuehr_r=-0.0000117)]))

    # 0.001176 − 0.0000117 = 0.0011643
    assert client.kaufe("BTCUSDT", 100.0).menge == pytest.approx(0.0011643)


@pytest.mark.parametrize("betrag", [0, -100])
def test_kauf_ohne_positiven_betrag_wirft_fehler_ohne_anfrage(client, betrag):
    with pytest.raises(ValueError, match="betrag muss grösser als 0"):
        client.kaufe("BTCUSDT", betrag)


# --- Verkaufen ---


@respx.mock
def test_verkauf_schickt_menge_in_btc_ohne_exponent(client):
    # str(0.00002) wäre "2e-05", das versteht Bitget nicht
    order = respx.post(ORDER_URL).respond(json=erfolg({"orderId": "2002"}))
    respx.get(INFO_URL).respond(json=erfolg([order_info(base_volume="0.00002")]))

    client.verkaufe("BTCUSDT", 0.00002)

    body = json.loads(order.calls.last.request.content)
    assert body["side"] == "sell"
    assert body["size"] == "0.00002"


@respx.mock
def test_verkauf_liefert_verkaufte_menge_ohne_gebuehrenabzug(client):
    # Beim Verkauf zieht Bitget die Gebühr von den erhaltenen USDT ab, nicht von den BTC
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "2002"}))
    respx.get(INFO_URL).respond(
        json=erfolg([{**order_info(base_volume="0.001173", preis="86000"), "orderId": "2002"}])
    )

    ergebnis = client.verkaufe("BTCUSDT", 0.001173)

    assert ergebnis == Order(order_id="2002", menge=0.001173, preis=86000.0)


@respx.mock
def test_teilweise_ausgefuehrter_verkauf_meldet_die_verkaufte_menge(client):
    # Verlangt 0.001173 BTC, im dünnen Demo-Markt wurden nur 0.0005 verkauft, der Rest verfällt
    # ("cancelled"). Gemeldet wird, was wirklich verkauft ist, nicht was verlangt war.
    respx.post(ORDER_URL).respond(json=erfolg({"orderId": "2002"}))
    respx.get(INFO_URL).respond(
        json=erfolg([{**order_info(status="cancelled", base_volume="0.0005"), "orderId": "2002"}])
    )

    ergebnis = client.verkaufe("BTCUSDT", 0.001173)

    assert ergebnis.menge == 0.0005


@pytest.mark.parametrize("menge", [0, -0.001])
def test_verkauf_ohne_positive_menge_wirft_fehler_ohne_anfrage(client, menge):
    with pytest.raises(ValueError, match="menge muss grösser als 0"):
        client.verkaufe("BTCUSDT", menge)
