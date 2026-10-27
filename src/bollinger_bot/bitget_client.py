"""BitgetClient: setzt den Port `Boerse` für Bitget um, Spot-Handel im Demo-Modus.

Der Client ist die einzige Stelle, die mit Bitget spricht. Er übersetzt zwischen der REST-API von
Bitget (Text, Millisekunden, verschachteltes JSON) und den Datenklassen aus `port.py`.

- **Demo-Modus:** Jede Anfrage trägt den Header `paptrading: 1`. Zusammen mit einem Demo-API-Key
  handelt Bitget dann mit Spielgeld.
- **Signatur:** Private Anfragen (Konto, Orders) sind mit HMAC-SHA256 signiert, siehe
  `signatur()`. Öffentliche Marktdaten (Kerzen) brauchen keine Signatur.
- **Fehler:** Antwortet Bitget mit einem Fehler oder gar nicht, wirft der Client `BitgetFehler`.
  Die Polling-Schleife protokolliert den Fehler und versucht es eine Minute später erneut.

API-Doku: https://www.bitget.com/docs/classic/rest-api (Classic-Konto, Endpunkte `/api/v2/spot/…`)
"""

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from bollinger_bot.port import Kerze

BASIS_URL = "https://api.bitget.com"

# Kerzen-Intervalle im Spot-Format von Bitget und wie lange eine Kerze dauert. Futures schreiben
# dieselben Intervalle anders ("15m" statt "15min"), darum nur die Spot-Schreibweise.
INTERVALLE = {
    "1min": timedelta(minutes=1),
    "5min": timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "30min": timedelta(minutes=30),
    "1h": timedelta(hours=1),
}

MAX_KERZEN = 1000  # mehr liefert Bitget pro Anfrage nicht


class BitgetFehler(Exception):
    """Bitget hat eine Anfrage abgelehnt, war nicht erreichbar oder hat Unerwartetes geliefert.

    `code` ist der Fehlercode von Bitget (z.B. "40018"), None wenn Bitget gar nicht geantwortet hat.
    """

    def __init__(self, meldung: str, *, code: str | None = None):
        super().__init__(meldung if code is None else f"{meldung} (Bitget-Code {code})")
        self.code = code


def signatur(
    *, secret: str, zeitstempel: str, methode: str, pfad: str, query: str = "", body: str = ""
) -> str:
    """Signatur für den Header `ACCESS-SIGN`.

    Bitget rechnet auf seiner Seite dasselbe nach. Stimmt ein Zeichen nicht überein (andere
    Reihenfolge der Parameter, Leerzeichen im JSON), lehnt Bitget die Anfrage ab.

    1. Nachricht: Zeitstempel + Methode in Grossbuchstaben + Pfad + "?" + Query + Body.
       Ohne Query fällt das "?" weg, ohne Body (GET) der Body.
    2. HMAC-SHA256 der Nachricht mit dem Secret als Schlüssel
    3. Ergebnis in Base64
    """
    nachricht = zeitstempel + methode.upper() + pfad + (f"?{query}" if query else "") + body
    mac = hmac.new(secret.encode(), nachricht.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _jetzt() -> datetime:
    return datetime.now(UTC)


def _als_zeit(millisekunden: str | int) -> datetime:
    return datetime.fromtimestamp(int(millisekunden) / 1000, UTC)


class BitgetClient:
    """Spricht mit Bitget im Demo-Modus. Erfüllt den Port `Boerse` aus `port.py`.

    Die Keys werden übergeben und nicht hier aus den Umgebungsvariablen gelesen: So können die
    Tests Fake-Keys übergeben, und nur der Startcode des Bots kennt die echten.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str,
        basis_url: str = BASIS_URL,
        timeout_sekunden: float = 10,
        uhr: Callable[[], datetime] = _jetzt,
    ):
        # Die Keys gehen als Header an Bitget. Ein Zeilenumbruch darin (z.B. aus einer .env mit
        # Windows-Zeilenenden) liesse httpx mit einer Meldung abbrechen, die den Wert im
        # Klartext enthält, und die landet dann jede Minute im Log. Darum hier prüfen, ohne den
        # Wert zu nennen.
        for name, wert in (
            ("api_key", api_key),
            ("api_secret", api_secret),
            ("passphrase", passphrase),
        ):
            if not wert or wert != wert.strip() or not wert.isprintable():
                raise ValueError(
                    f"{name} ist leer oder enthält Leerzeichen am Rand bzw. Steuerzeichen "
                    "(z.B. einen Zeilenumbruch). Bitte in der .env prüfen."
                )
        # Ohne Timeout liesse ein hängender Request den Bot ohne Fehlermeldung stillstehen.
        if timeout_sekunden is None or timeout_sekunden <= 0:
            raise ValueError(
                f"timeout_sekunden muss grösser als 0 sein, erhalten: {timeout_sekunden}"
            )
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._uhr = uhr  # nur für den Zeitstempel der Signatur, Tests übergeben eine feste Zeit
        self._http = httpx.Client(base_url=basis_url, timeout=timeout_sekunden)

    def schliessen(self) -> None:
        self._http.close()

    # --- Port `Boerse` ---

    def hole_kerzen(self, symbol: str, intervall: str, anzahl: int) -> list[Kerze]:
        """Die letzten `anzahl` abgeschlossenen Kerzen, älteste zuerst.

        Bitget liefert die laufende Kerze als letzte mit, ausser in den ersten 1 bis 2 Sekunden
        nach einem Kerzenschluss (gemessen am 21.10.2026). Darum zählt nicht die Position in der
        Liste: Abgeschlossen ist eine Kerze, wenn ihr Ende (Startzeit + Intervall) nicht nach der
        Serverzeit der Antwort liegt. Wir vergleichen mit der Uhr von Bitget (`requestTime`)
        statt mit der eigenen: Geht die Uhr der VM ein paar Sekunden vor, hielten wir sonst die
        laufende Kerze für abgeschlossen.
        """
        if intervall not in INTERVALLE:
            erlaubt = ", ".join(INTERVALLE)
            raise ValueError(f"Unbekanntes Intervall {intervall!r}, erlaubt: {erlaubt}")
        if not 1 <= anzahl < MAX_KERZEN:
            raise ValueError(
                f"anzahl muss zwischen 1 und {MAX_KERZEN - 1} liegen, erhalten: {anzahl}"
            )

        antwort = self._anfrage(
            "GET",
            "/api/v2/spot/market/candles",
            # Eine mehr als verlangt, weil die laufende Kerze gleich wieder wegfällt.
            params={"symbol": symbol, "granularity": intervall, "limit": str(anzahl + 1)},
            privat=False,
        )
        serverzeit = _als_zeit(antwort["requestTime"])
        dauer = INTERVALLE[intervall]

        kerzen = sorted(
            (self._als_kerze(zeile) for zeile in antwort["data"]), key=lambda k: k.startzeit
        )
        abgeschlossen = [k for k in kerzen if k.startzeit + dauer <= serverzeit]
        return abgeschlossen[-anzahl:]

    # --- Hilfsfunktionen ---

    @staticmethod
    def _als_kerze(zeile: list[str]) -> Kerze:
        # Bitget: [Startzeit in ms, Eröffnung, Hoch, Tief, Schluss, Volumen in BTC,
        # Volumen in USDT, Volumen in Quote-Währung], alles als Text
        try:
            return Kerze(
                startzeit=_als_zeit(zeile[0]),
                eroeffnung=float(zeile[1]),
                hoch=float(zeile[2]),
                tief=float(zeile[3]),
                schluss=float(zeile[4]),
                volumen=float(zeile[5]),
            )
        except (IndexError, TypeError, ValueError) as fehler:
            raise BitgetFehler(f"Unerwartetes Format einer Kerze: {zeile!r}") from fehler

    def _anfrage(
        self,
        methode: str,
        pfad: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
        privat: bool = True,
    ) -> dict[str, Any]:
        """Schickt eine Anfrage an Bitget und gibt die ganze Antwort zurück, falls erfolgreich.

        Erfolgreich heisst: HTTP-Status 200 und Bitget-Code "00000".
        """
        # Query und Body genau einmal in Text umwandeln: Derselbe Text wird signiert und gesendet.
        query = urlencode(params or {})
        body_text = json.dumps(body, separators=(",", ":")) if body is not None else ""

        headers = {"paptrading": "1", "Content-Type": "application/json", "locale": "en-US"}
        if privat:
            zeitstempel = str(int(self._uhr().timestamp() * 1000))
            headers |= {
                "ACCESS-KEY": self._api_key,
                "ACCESS-SIGN": signatur(
                    secret=self._api_secret,
                    zeitstempel=zeitstempel,
                    methode=methode,
                    pfad=pfad,
                    query=query,
                    body=body_text,
                ),
                "ACCESS-TIMESTAMP": zeitstempel,
                "ACCESS-PASSPHRASE": self._passphrase,
            }

        url = pfad + (f"?{query}" if query else "")
        try:
            antwort = self._http.request(methode, url, content=body_text or None, headers=headers)
        except httpx.HTTPError as fehler:
            raise BitgetFehler(f"Bitget nicht erreichbar: {fehler}") from fehler

        try:
            daten = antwort.json()
        except ValueError as fehler:
            raise BitgetFehler(
                f"Antwort von Bitget ist kein JSON (HTTP {antwort.status_code})"
            ) from fehler

        code = daten.get("code") if isinstance(daten, dict) else None
        if antwort.status_code != 200 or code != "00000":
            meldung = daten.get("msg", "") if isinstance(daten, dict) else ""
            raise BitgetFehler(
                f"Bitget lehnt {methode} {pfad} ab (HTTP {antwort.status_code}): {meldung}",
                code=code,
            )
        return daten
