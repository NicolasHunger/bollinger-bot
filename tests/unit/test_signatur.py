"""Unit-Tests für die Signatur der Bitget-Anfragen.

Die Soll-Werte sind nicht mit Python berechnet, sondern unabhängig mit openssl, z.B.:

    printf '%s' '16273667805456GET/api/v3/account/fee-rate?category=SPOT&symbol=BTCUSDT' \
        | openssl dgst -sha256 -hmac 'test-secret' -binary | base64

Die Nachricht im ersten Test stammt aus dem Beispiel der Bitget-Doku
(https://www.bitget.com/docs/uta/quick-start, Abschnitt «Signature»).
"""

from bollinger_bot.bitget_client import signatur

SECRET = "test-secret"


def test_get_mit_query_wie_im_beispiel_der_doku():
    # Nachricht: 16273667805456GET/api/v3/account/fee-rate?category=SPOT&symbol=BTCUSDT
    ergebnis = signatur(
        secret=SECRET,
        zeitstempel="16273667805456",
        methode="GET",
        pfad="/api/v3/account/fee-rate",
        query="category=SPOT&symbol=BTCUSDT",
    )

    assert ergebnis == "TdABQQ9ijAseDaXVnIRMSNNiP8a2N3JfNvKnXjSB3EE="


def test_post_mit_body_ohne_query():
    # Nachricht ohne "?": 1790773200000POST/api/v2/spot/trade/place-order{"symbol":"BTCUSDT",…}
    body = '{"symbol":"BTCUSDT","side":"buy","orderType":"market","force":"gtc","size":"100"}'

    ergebnis = signatur(
        secret=SECRET,
        zeitstempel="1790773200000",
        methode="POST",
        pfad="/api/v2/spot/trade/place-order",
        body=body,
    )

    assert ergebnis == "8YR2gz4C//tJP1ziHhSZM8iuU+TWyvTIT9rsjIKNV+Y="


def test_methode_wird_in_grossbuchstaben_signiert():
    # "get" und "GET" ergeben dieselbe Nachricht, Bitget rechnet immer mit Grossbuchstaben
    argumente = dict(
        secret=SECRET,
        zeitstempel="1790773200000",
        pfad="/api/v2/spot/market/candles",
        query="symbol=BTCUSDT&granularity=15min&limit=3",
    )

    assert signatur(methode="get", **argumente) == signatur(methode="GET", **argumente)
    assert signatur(methode="GET", **argumente) == "vDeP8Jf4eZxPYxx9of9nYSjCnFWN2873uD9H497PvFQ="


def test_anderes_secret_ergibt_andere_signatur():
    argumente = dict(zeitstempel="1790773200000", methode="GET", pfad="/api/v2/spot/account/assets")

    assert signatur(secret="a", **argumente) != signatur(secret="b", **argumente)


def test_query_reihenfolge_zaehlt():
    # Bitget signiert den Text so, wie er in der URL steht. Darum muss der Client genau den
    # signierten Text auch senden.
    argumente = dict(secret=SECRET, zeitstempel="1", methode="GET", pfad="/p")

    assert signatur(query="a=1&b=2", **argumente) != signatur(query="b=2&a=1", **argumente)
