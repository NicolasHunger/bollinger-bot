"""Holt abgeschlossene BTCUSDT-Kerzen vom öffentlichen Bitget-Endpunkt und speichert sie als CSV.

Aufruf: python scripts/kerzen_holen.py <ziel.csv> [anzahl]

Der Endpunkt `history-candles` ist öffentlich (kein API-Key) und liefert höchstens 200 Kerzen pro
Anfrage, darum wird seitenweise rückwärts geholt. Die laufende Kerze ist nicht dabei: `endTime`
ist der Beginn der laufenden Kerze, Bitget liefert nur Kerzen, die davor beginnen.
"""

import csv
import sys
import time
from datetime import UTC, datetime

import httpx

URL = "https://api.bitget.com/api/v2/spot/market/history-candles"
INTERVALL_MS = 15 * 60 * 1000


def hole(anzahl: int) -> list[list[str]]:
    jetzt_ms = int(time.time() * 1000)
    ende = jetzt_ms - jetzt_ms % INTERVALL_MS  # Beginn der laufenden Kerze
    zeilen: dict[int, list[str]] = {}
    with httpx.Client(timeout=20) as client:
        while len(zeilen) < anzahl:
            antwort = client.get(
                URL,
                params={"symbol": "BTCUSDT", "granularity": "15min", "endTime": ende, "limit": 200},
            )
            antwort.raise_for_status()
            daten = antwort.json()
            if daten["code"] != "00000" or not daten["data"]:
                raise RuntimeError(f"Bitget: {daten['code']} {daten['msg']}")
            for zeile in daten["data"]:
                zeilen[int(zeile[0])] = zeile
            ende = min(int(zeile[0]) for zeile in daten["data"])
            time.sleep(0.2)  # höflich bleiben, Grenze laut Doku 20 Anfragen pro Sekunde
    return [zeilen[ts] for ts in sorted(zeilen)][-anzahl:]


def schreibe(pfad: str, zeilen: list[list[str]]) -> None:
    with open(pfad, "w", newline="") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["startzeit", "eroeffnung", "hoch", "tief", "schluss", "volumen"])
        for zeile in zeilen:
            startzeit = datetime.fromtimestamp(int(zeile[0]) / 1000, UTC).isoformat()
            schreiber.writerow([startzeit, *zeile[1:6]])


if __name__ == "__main__":
    ziel = sys.argv[1]
    anzahl = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    schreibe(ziel, hole(anzahl))
    print(f"{anzahl} Kerzen nach {ziel} geschrieben")
