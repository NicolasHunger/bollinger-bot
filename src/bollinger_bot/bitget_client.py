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
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from bollinger_bot.port import Kerze, Order

log = logging.getLogger(__name__)

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

# Eine Market-Order ist meist nach wenigen Millisekunden ausgeführt. Wir fragen den Status bis zu
# 10-mal im Abstand von einer halben Sekunde ab, bevor wir aufgeben.
ORDER_ABFRAGEN = 10
ORDER_PAUSE_SEKUNDEN = 0.5

# Gebühr im Spot-Demo, gemessen im Rauchtest vom 21.10.2026. Nur der Ersatz, wenn Bitget die
# Gebühr nicht lesbar oder unplausibel meldet, sonst gilt der Wert aus der Antwort.
GEBUEHR_SATZ = 0.001


class BitgetFehler(Exception):
    """Bitget hat eine Anfrage abgelehnt, war nicht erreichbar oder hat Unerwartetes geliefert.

    `code` ist der Fehlercode von Bitget (z.B. "40018"), None wenn Bitget gar nicht geantwortet hat.
    """

    def __init__(self, meldung: str, *, code: str | None = None):
        super().__init__(meldung if code is None else f"{meldung} (Bitget-Code {code})")
        self.code = code


class OrderFehler(BitgetFehler):
    """Eine Order ist bei Bitget platziert, ihr Ergebnis aber nicht bekannt.

    Vielleicht wurde sie trotzdem ausgeführt. `order_id` sagt, welche Order es war, damit man sie
    im Log findet und auf Bitget nachschauen kann. Wer den Fehler fängt (z.B. `tick()`), weiss so
    auch, dass er nicht einfach noch einmal kaufen darf.
    """

    def __init__(self, meldung: str, *, order_id: str, code: str | None = None):
        super().__init__(meldung, code=code)
        self.order_id = order_id


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


def _als_text(zahl: float) -> str:
    """Zahl als Text ohne Exponent: `str(0.00001)` ergibt "1e-05", Bitget will "0.00001"."""
    return format(Decimal(str(zahl)), "f")


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
        schlafen: Callable[[float], None] = time.sleep,
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
        self._schlafen = schlafen  # Warten auf die Ausführung einer Order, Tests warten nicht echt
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

    def hole_kontostand(self, waehrung: str) -> float:
        """Frei verfügbares Guthaben, z.B. `hole_kontostand("USDT")`. 0.0, wenn keins da ist.

        Frei verfügbar (`available`) heisst: ohne Beträge in offenen Orders (`frozen`).
        """
        antwort = self._anfrage("GET", "/api/v2/spot/account/assets", params={"coin": waehrung})
        for eintrag in antwort["data"] or []:
            # Bitget schreibt die Währung mal gross, mal klein ("usdt" im Beispiel der Doku)
            if eintrag.get("coin", "").upper() == waehrung.upper():
                return float(eintrag["available"])
        return 0.0

    def kaufe(self, symbol: str, betrag: float) -> Order:
        """Market-Order: kauft sofort für `betrag` USDT.

        Bitget will bei einer Market-Kauforder den Betrag in USDT, nicht die Menge in BTC: Wie
        viel BTC man für 100 USDT bekommt, steht erst nach der Ausführung fest. Den Betrag hat der
        Risiko-Check schon abgerundet (`risiko.kaufbetrag`).

        Die zurückgegebene Menge ist schon ohne Gebühr: Bitget zieht die Gebühr beim Kauf von den
        gekauften BTC ab. Genau diese Menge liegt danach auf dem Konto und lässt sich verkaufen.
        Bitget rundet die gekaufte Menge auf 6 Stellen, darum wird nicht der ganze Betrag
        ausgegeben (Rauchtest: für 5 USDT wurden 4.92 USDT ausgegeben).
        """
        if betrag <= 0:
            raise ValueError(f"betrag muss grösser als 0 sein, erhalten: {betrag}")
        order_id = self._platziere(symbol, seite="buy", groesse=betrag)
        info = self._warte_auf_ausfuehrung(order_id)
        gekauft, preis = self._menge_und_preis(order_id, info)
        gebuehr = self._gebuehr(info)
        # Bitget verlangt im Spot-Demo 0.1 % Gebühr. Ist `feeDetail` nicht lesbar oder der Abzug
        # 1 % oder mehr, stimmt etwas nicht. Dann die bekannten 0.1 % abziehen: Ohne Abzug wäre
        # die gemeldete Menge zu gross, und jeder Verkauf scheiterte mit 43012 «Insufficient
        # balance», auch der Stop-Loss. Lieber ein kleiner BTC-Rest auf dem Konto.
        if gebuehr is None or gebuehr >= gekauft * 0.01:
            log.warning(
                "Gebühr %s bei %s gekauft nicht lesbar oder unplausibel, rechne mit %s %%",
                gebuehr,
                gekauft,
                GEBUEHR_SATZ * 100,
            )
            gebuehr = gekauft * GEBUEHR_SATZ
        return Order(order_id=order_id, menge=gekauft - gebuehr, preis=preis)

    def verkaufe(self, symbol: str, menge: float) -> Order:
        """Market-Order: verkauft sofort `menge` BTC.

        Die Menge hat der Risiko-Check schon auf die erlaubten Stellen abgerundet
        (`risiko.verkaufsmenge`). Die Gebühr zieht Bitget beim Verkauf von den erhaltenen USDT ab,
        die verkaufte Menge bleibt also gleich.
        """
        if menge <= 0:
            raise ValueError(f"menge muss grösser als 0 sein, erhalten: {menge}")
        order_id = self._platziere(symbol, seite="sell", groesse=menge)
        info = self._warte_auf_ausfuehrung(order_id)
        # Im dünnen Demo-Markt kann auch ein Verkauf nur teilweise ausgeführt werden. Gemeldet
        # wird die tatsächlich verkaufte Menge (`baseVolume`), nicht die verlangte.
        verkauft, preis = self._menge_und_preis(order_id, info)
        return Order(order_id=order_id, menge=verkauft, preis=preis)

    # --- Hilfsfunktionen ---

    def _platziere(self, symbol: str, *, seite: str, groesse: float) -> str:
        """Schickt eine Market-Order und gibt die Order-ID zurück.

        `groesse` ist beim Kauf ein Betrag in USDT, beim Verkauf eine Menge in BTC.
        """
        antwort = self._anfrage(
            "POST",
            "/api/v2/spot/trade/place-order",
            body={
                "symbol": symbol,
                "side": seite,
                "orderType": "market",
                "force": "gtc",  # verlangt Bitget immer, wirkt aber nur bei Limit-Orders
                "size": _als_text(groesse),
            },
        )
        return antwort["data"]["orderId"]

    def _warte_auf_ausfuehrung(self, order_id: str) -> dict[str, Any]:
        """Fragt den Status der Order ab, bis sie ausgeführt ist, und gibt die Details zurück.

        Bitget bestätigt beim Platzieren nur die Order-ID. Preis, Menge und Gebühr stehen erst in
        `orderInfo`. Eine Market-Order endet als `filled` (ganz ausgeführt) oder `cancelled` (im
        dünnen Demo-Markt nur teilweise ausgeführt, der Rest verfällt).

        Fällt eine einzelne Abfrage aus (Netzfehler, HTTP 429, Fehlercode), fragt der Client beim
        nächsten Versuch weiter. Die Order ist ja schon platziert: Bräche er sofort ab, wüsste
        `tick()` nichts vom Kauf und kaufte eine Minute später noch einmal.

        Wirft `OrderFehler` mit `order_id`, wenn das Ergebnis am Ende nicht feststeht.
        """
        letzter_fehler: BitgetFehler | None = None
        for versuch in range(ORDER_ABFRAGEN):
            try:
                antwort = self._anfrage(
                    "GET", "/api/v2/spot/trade/orderInfo", params={"orderId": order_id}
                )
            except BitgetFehler as fehler:
                log.warning(
                    "Abfrage %s von Order %s fehlgeschlagen: %s", versuch + 1, order_id, fehler
                )
                letzter_fehler = fehler
            else:
                infos = antwort["data"] or []  # kurz nach dem Platzieren manchmal noch leer
                # "live" und "partially_filled" sind Zwischenstände, also weiter abfragen
                if infos and infos[0].get("status") in ("filled", "cancelled"):
                    info = infos[0]
                    if float(info.get("baseVolume") or 0) <= 0:
                        raise OrderFehler(
                            f"Order {order_id} wurde nicht ausgeführt", order_id=order_id
                        )
                    return info
            if versuch < ORDER_ABFRAGEN - 1:
                self._schlafen(ORDER_PAUSE_SEKUNDEN)
        # Die Order kann danach trotzdem noch ausgeführt werden.
        meldung = f"Order {order_id} nach {ORDER_ABFRAGEN} Abfragen nicht bestätigt"
        if letzter_fehler is not None:
            meldung += f", zuletzt: {letzter_fehler}"
        raise OrderFehler(meldung, order_id=order_id) from letzter_fehler

    @staticmethod
    def _menge_und_preis(order_id: str, info: dict[str, Any]) -> tuple[float, float]:
        """Ausgeführte Menge in BTC und Durchschnittspreis aus `orderInfo`.

        Die Order ist zu diesem Zeitpunkt schon ausgeführt. Fehlt ein Feld, kommt darum ein
        `OrderFehler` mit der Order-ID, kein gewöhnlicher Fehler.
        """
        try:
            return float(info["baseVolume"]), float(info["priceAvg"])
        except (KeyError, TypeError, ValueError) as fehler:
            raise OrderFehler(
                f"Order {order_id} ausgeführt, aber Menge oder Preis nicht lesbar: {fehler!r}",
                order_id=order_id,
            ) from fehler

    @staticmethod
    def _gebuehr(info: dict[str, Any]) -> float | None:
        """Gebühr, die Bitget von der gekauften Währung abgezogen hat, als positive Zahl.

        None, wenn `feeDetail` nicht lesbar ist.

        `feeDetail` ist JSON als Text. Unter `newFees.r` steht, was von der erhaltenen Währung
        abgezogen wurde (negativ). Wer die Gebühr mit BGB-Rabatt bezahlt, hat dort 0.
        """
        try:
            details = json.loads(info.get("feeDetail") or "{}")
            return abs(float(details.get("newFees", {}).get("r", 0)))
        except (ValueError, TypeError, AttributeError):
            # Die Order ist zu diesem Zeitpunkt schon ausgeführt. Ein Fehler hier darf den Trade
            # nicht verschwinden lassen: `kaufe` rechnet dann mit den bekannten 0.1 %.
            log.warning("Gebühr nicht lesbar: %r", info.get("feeDetail"))
            return None

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
