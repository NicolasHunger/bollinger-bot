"""Rauchtest von Hand gegen die echte Bitget-Demo-API (siehe Testkonzept im README).

Prüft, was die Mocks in den automatischen Tests nicht prüfen können: ob Bitget unsere Signatur
annimmt, ob das Demo-Konto die Classic-Endpunkte `/api/v2/spot/…` verwendet und wie die Gebühr
in einer echten Order aussieht. Läuft nie in der Pipeline und liegt darum nicht im Paket
`src/bollinger_bot/`: So zählt es auch nicht zur Coverage.

Die Demo-Keys kommen aus den Umgebungsvariablen oder aus der Datei `.env` im Projektordner
(gitignored):

    BITGET_API_KEY=...
    BITGET_API_SECRET=...
    BITGET_PASSPHRASE=...

Aufruf:

    python scripts/rauchtest.py           # nur lesen: Kerzen und Kontostand
    python scripts/rauchtest.py --order   # zusätzlich Kauf für 5 USDT und Verkauf

Voraussetzung: das Projekt ist installiert (`pip install -e ".[dev]"`).

Gibt nie einen Key aus.
"""

import json
import os
import sys
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from bollinger_bot.bitget_client import BitgetClient, BitgetFehler

SYMBOL = "BTCUSDT"
TESTBETRAG = 5.0  # USDT, klein, aber über dem Mindestbetrag von 1 USDT
SCHLUESSEL = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE")


def lies_env_datei(pfad: Path) -> None:
    """Übernimmt `NAME=wert`-Zeilen aus `.env`, ausser die Variable ist schon gesetzt."""
    if not pfad.exists():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, wert = zeile.split("=", 1)
        os.environ.setdefault(name.strip(), wert.strip().strip('"').strip("'"))


def schritt(titel: str) -> None:
    print(f"\n=== {titel}")


def main() -> int:
    lies_env_datei(Path.cwd() / ".env")
    fehlend = [name for name in SCHLUESSEL if not os.environ.get(name)]
    if fehlend:
        print(f"Fehlt: {', '.join(fehlend)}. In .env eintragen oder als Umgebungsvariable setzen.")
        return 2

    client = BitgetClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_PASSPHRASE"],
    )
    try:
        schritt("1. Kerzen (öffentlich, ohne Signatur)")
        kerzen = client.hole_kerzen(SYMBOL, "15min", 3)
        for kerze in kerzen:
            print(f"{kerze.startzeit:%Y-%m-%d %H:%M} UTC  Schluss {kerze.schluss}")

        schritt("2. Kontostand (privat, prüft die Signatur)")
        usdt = client.hole_kontostand("USDT")
        btc = client.hole_kontostand("BTC")
        print(f"USDT frei: {usdt}\nBTC frei:  {btc}")

        if "--order" not in sys.argv:
            print("\nOhne --order fertig. Kerzen und Signatur funktionieren.")
            return 0

        schritt(f"3. Kauf für {TESTBETRAG} USDT (Demo-Geld)")
        kauf = client.kaufe(SYMBOL, TESTBETRAG)
        print(f"Order {kauf.order_id}: {kauf.menge} BTC zu {kauf.preis} USDT (Menge ohne Gebühr)")
        # Rohdaten der Order: Daran sehen wir, wie `feeDetail` wirklich aussieht.
        info = client._anfrage(
            "GET", "/api/v2/spot/trade/orderInfo", params={"orderId": kauf.order_id}
        )["data"][0]
        felder = ("status", "baseVolume", "quoteVolume", "priceAvg", "feeDetail")
        print("Rohdaten:", json.dumps({k: info.get(k) for k in felder}, indent=2))
        print(f"BTC frei nach dem Kauf: {client.hole_kontostand('BTC')}")

        schritt("4. Verkauf der gekauften Menge")
        # Auf 6 Stellen abrunden wie im Risiko-Check (der liegt in einem eigenen PR)
        menge = float(Decimal(str(kauf.menge)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN))
        verkauf = client.verkaufe(SYMBOL, menge)
        print(f"Order {verkauf.order_id}: {verkauf.menge} BTC zu {verkauf.preis} USDT")
        print(f"\nUSDT frei am Ende: {client.hole_kontostand('USDT')}")
        return 0
    except BitgetFehler as fehler:
        print(f"\nBitgetFehler: {fehler}")
        return 1
    finally:
        client.schliessen()


if __name__ == "__main__":
    sys.exit(main())
