"""Integrationstests: Signierte Anfragen kommen so bei Bitget an, wie sie signiert wurden.

Die Signatur allein ist in `tests/unit/test_signatur.py` getestet. Hier geht es darum, dass der
Client genau den Text sendet, den er signiert hat: Würde httpx die Query umsortieren oder das
JSON anders formatieren, lehnte Bitget jede private Anfrage ab.

Soll-Signaturen mit openssl berechnet, Secret "fake-secret", z.B.:

    printf '%s' '1790773200000GET/api/v2/spot/account/assets?coin=USDT' \
        | openssl dgst -sha256 -hmac 'fake-secret' -binary | base64
"""

from datetime import UTC, datetime

import pytest
import respx

from bollinger_bot.bitget_client import BitgetClient

JETZT = datetime(2026, 9, 30, 13, 0, tzinfo=UTC)  # = 1790773200000 ms


@pytest.fixture
def client():
    client = BitgetClient(
        api_key="fake-key", api_secret="fake-secret", passphrase="fake-pass", uhr=lambda: JETZT
    )
    yield client
    client.schliessen()


@respx.mock
def test_get_traegt_alle_header_fuer_die_signatur(client):
    route = respx.get("https://api.bitget.com/api/v2/spot/account/assets").respond(
        json={"code": "00000", "msg": "success", "requestTime": 1, "data": []}
    )

    client._anfrage("GET", "/api/v2/spot/account/assets", params={"coin": "USDT"})

    anfrage = route.calls.last.request
    assert anfrage.url.query == b"coin=USDT"  # genau der signierte Text
    assert anfrage.headers["ACCESS-KEY"] == "fake-key"
    assert anfrage.headers["ACCESS-PASSPHRASE"] == "fake-pass"
    assert anfrage.headers["ACCESS-TIMESTAMP"] == "1790773200000"
    # Nachricht: 1790773200000GET/api/v2/spot/account/assets?coin=USDT
    assert anfrage.headers["ACCESS-SIGN"] == "DLfYbBYyIvpv8YbqLs18HDHblZPhNO7wTkdBA0OgWx0="
    assert anfrage.headers["paptrading"] == "1"


@respx.mock
def test_mehrere_parameter_bleiben_in_derselben_reihenfolge(client):
    # Bitget signiert die Query so, wie sie in der URL steht. Würde der Client die Parameter beim
    # Signieren sortieren (orderId vor symbol), wiche die Signatur von der gesendeten URL ab.
    route = respx.get("https://api.bitget.com/api/v2/spot/trade/fills").respond(
        json={"code": "00000", "msg": "success", "requestTime": 1, "data": []}
    )

    client._anfrage("GET", "/api/v2/spot/trade/fills", params={"symbol": "BTCUSDT", "orderId": "1"})

    anfrage = route.calls.last.request
    assert anfrage.url.query == b"symbol=BTCUSDT&orderId=1"
    # Nachricht: 1790773200000GET/api/v2/spot/trade/fills?symbol=BTCUSDT&orderId=1
    assert anfrage.headers["ACCESS-SIGN"] == "jUX4exHdy2lGLL5Nj3v/JDaUuNhg2hDiZAW1haeeYxg="


@respx.mock
def test_post_sendet_den_signierten_body_ohne_leerzeichen(client):
    route = respx.post("https://api.bitget.com/api/v2/spot/trade/place-order").respond(
        json={"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "1"}}
    )
    body = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "orderType": "market",
        "force": "gtc",
        "size": "100",
    }

    client._anfrage("POST", "/api/v2/spot/trade/place-order", body=body)

    anfrage = route.calls.last.request
    assert anfrage.content == (
        b'{"symbol":"BTCUSDT","side":"buy","orderType":"market","force":"gtc","size":"100"}'
    )
    assert anfrage.headers["Content-Type"] == "application/json"
    # Nachricht: 1790773200000POST/api/v2/spot/trade/place-order{"symbol":"BTCUSDT",…}
    assert anfrage.headers["ACCESS-SIGN"] == "xK3Hgc3TT4n31mYr+253SxzafKxaj8yKBAaw6S8Mtjs="
