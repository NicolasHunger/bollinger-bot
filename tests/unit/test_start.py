"""Unit-Tests für das Lesen der Konfiguration aus den Umgebungsvariablen.

Die Tests übergeben ein eigenes Dictionary statt `os.environ`, so ändert kein Test die echte
Umgebung. Die Keys sind erfunden.
"""

from datetime import timedelta
from pathlib import Path

from bollinger_bot.start import lies_konfiguration
from bollinger_bot.tick import Einstellungen

KEYS = {
    "BITGET_API_KEY": "fake-key",
    "BITGET_API_SECRET": "fake-secret",
    "BITGET_PASSPHRASE": "fake-passphrase",
}


def test_alle_werte_gesetzt():
    konfiguration = lies_konfiguration(
        KEYS | {"BOT_DB_PFAD": "/daten/bot.db", "BOT_INTERVALL": "5min"}
    )

    assert konfiguration.api_key == "fake-key"
    assert konfiguration.api_secret == "fake-secret"
    assert konfiguration.passphrase == "fake-passphrase"
    assert konfiguration.db_pfad == Path("/daten/bot.db")
    # "5min" ergibt Kerzen von 5 Minuten, alles andere bleibt beim Standard (BTCUSDT)
    assert konfiguration.einstellungen.intervall_dauer == timedelta(minutes=5)
    assert konfiguration.einstellungen == Einstellungen(
        intervall="5min", intervall_dauer=timedelta(minutes=5)
    )
