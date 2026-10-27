"""Integrationstests: BitgetClient holt Kerzen, Bitget ist mit respx gemockt.

respx fängt jede Anfrage ab, die httpx schicken will, und antwortet mit dem, was der Test
vorgibt. Es geht nie eine Anfrage ins Netz: Eine Anfrage ohne passende Vorgabe lässt den Test
fehlschlagen.

Die Beispiel-Antworten haben dieselbe Form wie echte Antworten der Demo-API (abgefragt am
30.09.2026 um 13:03:41 UTC mit 15-Minuten-Kerzen):
- Kerze ab 12:30 (1790771400000 ms) und ab 12:45 (1790772300000 ms): abgeschlossen
- Kerze ab 13:00 (1790773200000 ms): läuft noch, endet erst um 13:15
- `requestTime` 1790773421524 ms = 13:03:41.524 UTC, die Uhr von Bitget
"""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from bollinger_bot.bitget_client import INTERVALLE, BitgetClient, BitgetFehler
from bollinger_bot.port import Kerze

KERZEN_URL = "https://api.bitget.com/api/v2/spot/market/candles"

ANTWORT = {
    "code": "00000",
    "msg": "success",
    "requestTime": 1790773421524,
    "data": [
        ["1790771400000", "83909.05", "85006.78", "83909.05", "84792.51", "0.375998", "31765.51", "31765.51"],
        ["1790772300000", "84792.51", "85462.06", "84772.91", "85454.99", "0.272113", "23171.37", "23171.37"],
        ["1790773200000", "85454.99", "85454.99", "85088.7", "85174.35", "0.027189", "2315.13", "2315.13"],
    ],
}  # fmt: skip


@pytest.fixture
def client():
    client = BitgetClient(api_key="fake-key", api_secret="fake-secret", passphrase="fake-pass")
    yield client
    client.schliessen()


@respx.mock
def test_liefert_nur_abgeschlossene_kerzen_aelteste_zuerst(client):
    respx.get(KERZEN_URL).respond(json=ANTWORT)

    kerzen = client.hole_kerzen("BTCUSDT", "15min", 2)

    assert kerzen == [
        Kerze(
            startzeit=datetime(2026, 9, 30, 12, 30, tzinfo=UTC),
            eroeffnung=83909.05,
            hoch=85006.78,
            tief=83909.05,
            schluss=84792.51,
            volumen=0.375998,
        ),
        Kerze(
            startzeit=datetime(2026, 9, 30, 12, 45, tzinfo=UTC),
            eroeffnung=84792.51,
            hoch=85462.06,
            tief=84772.91,
            schluss=85454.99,
            volumen=0.272113,
        ),
    ]


@respx.mock
def test_fragt_eine_kerze_mehr_an_und_sendet_den_demo_header(client):
    route = respx.get(KERZEN_URL).respond(json=ANTWORT)

    client.hole_kerzen("BTCUSDT", "15min", 2)

    anfrage = route.calls.last.request
    assert anfrage.url.params["symbol"] == "BTCUSDT"
    assert anfrage.url.params["granularity"] == "15min"
    assert anfrage.url.params["limit"] == "3"  # 2 verlangt, +1 für die laufende Kerze
    assert anfrage.headers["paptrading"] == "1"


@respx.mock
def test_kerzen_brauchen_keine_signatur(client):
    # Marktdaten sind öffentlich: Die Keys gehen dafür nicht an Bitget.
    route = respx.get(KERZEN_URL).respond(json=ANTWORT)

    client.hole_kerzen("BTCUSDT", "15min", 2)

    assert "ACCESS-KEY" not in route.calls.last.request.headers
    assert "ACCESS-SIGN" not in route.calls.last.request.headers


@respx.mock
def test_sortiert_auch_wenn_bitget_neueste_zuerst_liefert(client):
    umgekehrt = {**ANTWORT, "data": list(reversed(ANTWORT["data"]))}
    respx.get(KERZEN_URL).respond(json=umgekehrt)

    kerzen = client.hole_kerzen("BTCUSDT", "15min", 2)

    assert [k.startzeit.minute for k in kerzen] == [30, 45]


@respx.mock
def test_kerze_genau_beim_schluss_gilt_als_abgeschlossen(client):
    # Grenzwert: Serverzeit genau 13:00:00.000, die Kerze ab 12:45 endet genau jetzt.
    # Kurz nach dem Kerzenschluss hat Bitget die neue Kerze noch nicht angelegt (gemessen am
    # 21.10.2026: 1 bis 2 Sekunden lang). Dann ist die letzte Zeile schon abgeschlossen. Darum
    # zählt die Serverzeit und nicht «die letzte Zeile fällt immer weg».
    genau = {**ANTWORT, "requestTime": 1790773200000, "data": ANTWORT["data"][:2]}
    respx.get(KERZEN_URL).respond(json=genau)

    kerzen = client.hole_kerzen("BTCUSDT", "15min", 2)

    assert [k.startzeit.minute for k in kerzen] == [30, 45]


@respx.mock
def test_kerze_eine_millisekunde_vor_dem_schluss_laeuft_noch(client):
    # Grenzwert: Serverzeit 12:59:59.999, die Kerze ab 12:45 läuft noch
    knapp = {**ANTWORT, "requestTime": 1790773199999, "data": ANTWORT["data"][:2]}
    respx.get(KERZEN_URL).respond(json=knapp)

    kerzen = client.hole_kerzen("BTCUSDT", "15min", 2)

    assert [k.startzeit.minute for k in kerzen] == [30]


@respx.mock
def test_in_der_luecke_nach_dem_schluss_kommen_die_neuesten_kerzen(client):
    # Serverzeit 13:00:01, Bitget hat die Kerze ab 13:00 noch nicht angelegt. Darum sind alle drei
    # gelieferten Zeilen (12:15, 12:30, 12:45) abgeschlossen, verlangt sind aber nur 2. Gemeint
    # sind die zwei neuesten, nicht die zwei ältesten.
    kerze_1215 = ["1790770500000", "83000", "83100", "82900", "83050", "0.1", "8305", "8305"]
    luecke = {**ANTWORT, "requestTime": 1790773201000, "data": [kerze_1215, *ANTWORT["data"][:2]]}
    respx.get(KERZEN_URL).respond(json=luecke)

    kerzen = client.hole_kerzen("BTCUSDT", "15min", 2)

    assert [k.startzeit.minute for k in kerzen] == [30, 45]


@pytest.mark.parametrize(("intervall", "dauer"), list(INTERVALLE.items()))
@pytest.mark.parametrize(
    ("abstand_ms", "abgeschlossen"),
    [(0, True), (-1, False)],  # genau beim Schluss / 1 ms davor
)
@respx.mock
def test_grenze_beim_schluss_gilt_fuer_alle_intervalle(
    client, intervall, dauer, abstand_ms, abgeschlossen
):
    # Kerze ab 13:00 UTC (1790773200000 ms), sie endet nach `dauer`. Die Serverzeit liegt genau
    # auf diesem Ende oder 1 ms davor.
    start_ms = 1790773200000
    ende_ms = start_ms + int(dauer.total_seconds() * 1000)
    zeile = [str(start_ms), "83000", "83100", "82900", "83050", "0.1", "8305", "8305"]
    respx.get(KERZEN_URL).respond(
        json={**ANTWORT, "requestTime": ende_ms + abstand_ms, "data": [zeile]}
    )

    kerzen = client.hole_kerzen("BTCUSDT", intervall, 1)

    assert len(kerzen) == (1 if abgeschlossen else 0)


@respx.mock
def test_leere_antwort_ergibt_leere_liste(client):
    respx.get(KERZEN_URL).respond(json={**ANTWORT, "data": []})

    assert client.hole_kerzen("BTCUSDT", "15min", 20) == []


@respx.mock
def test_fehlercode_von_bitget_wird_als_bitget_fehler_geworfen(client):
    # Echte Antwort von Bitget bei einem unbekannten Handelspaar, HTTP 400
    respx.get(KERZEN_URL).respond(
        400,
        json={"code": "40034", "msg": "Parameter GIBTESNICHT does not exist", "data": None},
    )

    with pytest.raises(BitgetFehler, match="Bitget-Code 40034") as fehler:
        client.hole_kerzen("GIBTESNICHT", "15min", 2)

    assert fehler.value.code == "40034"


@respx.mock
def test_fehlercode_bei_http_200_wird_als_bitget_fehler_geworfen(client):
    # Bitget meldet manche Fehler mit HTTP 200 und einem Code ungleich "00000". Ohne die Prüfung
    # des Codes würde der Client dann `data: null` als Kerzen lesen.
    respx.get(KERZEN_URL).respond(
        200, json={"code": "40019", "msg": "Parameter cannot be empty", "data": None}
    )

    with pytest.raises(BitgetFehler, match="Bitget-Code 40019") as fehler:
        client.hole_kerzen("BTCUSDT", "15min", 2)

    assert fehler.value.code == "40019"


@respx.mock
def test_netzwerkfehler_wird_als_bitget_fehler_geworfen(client):
    respx.get(KERZEN_URL).mock(side_effect=httpx.ConnectTimeout("Zeitüberschreitung"))

    with pytest.raises(BitgetFehler, match="Bitget nicht erreichbar") as fehler:
        client.hole_kerzen("BTCUSDT", "15min", 2)

    assert fehler.value.code is None


@respx.mock
def test_antwort_ohne_json_wird_als_bitget_fehler_geworfen(client):
    # z.B. eine HTML-Fehlerseite eines Proxys
    respx.get(KERZEN_URL).respond(502, text="<html>Bad Gateway</html>")

    with pytest.raises(BitgetFehler, match="kein JSON"):
        client.hole_kerzen("BTCUSDT", "15min", 2)


@respx.mock
def test_kaputte_kerze_wird_als_bitget_fehler_geworfen(client):
    respx.get(KERZEN_URL).respond(json={**ANTWORT, "data": [["1790771400000", "kein Kurs"]]})

    with pytest.raises(BitgetFehler, match="Unerwartetes Format"):
        client.hole_kerzen("BTCUSDT", "15min", 2)


def test_unbekanntes_intervall_wirft_fehler_ohne_anfrage(client):
    # "15m" ist die Futures-Schreibweise, Spot schreibt "15min"
    with pytest.raises(ValueError, match="Unbekanntes Intervall '15m'"):
        client.hole_kerzen("BTCUSDT", "15m", 2)


@pytest.mark.parametrize("anzahl", [0, 1000])
def test_anzahl_ausserhalb_der_grenzen_wirft_fehler(client, anzahl):
    # Grenzwerte: mindestens 1, höchstens 999, weil Bitget 1000 liefert und eine wegfällt
    with pytest.raises(ValueError, match="anzahl muss zwischen 1 und 999"):
        client.hole_kerzen("BTCUSDT", "15min", anzahl)


@respx.mock
def test_anzahl_999_ist_erlaubt(client):
    route = respx.get(KERZEN_URL).respond(json=ANTWORT)

    client.hole_kerzen("BTCUSDT", "15min", 999)

    assert route.calls.last.request.url.params["limit"] == "1000"
