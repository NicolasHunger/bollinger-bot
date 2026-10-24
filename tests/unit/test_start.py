"""Unit-Tests für das Lesen der Konfiguration aus den Umgebungsvariablen.

Die Tests übergeben ein eigenes Dictionary statt `os.environ`, so ändert kein Test die echte
Umgebung. Die Keys sind erfunden.
"""

from datetime import timedelta
from pathlib import Path

import pytest

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


def test_ohne_pfad_und_intervall_gelten_die_standardwerte():
    konfiguration = lies_konfiguration(KEYS)

    assert konfiguration.db_pfad == Path("data/bot.db")
    assert konfiguration.einstellungen == Einstellungen()  # BTCUSDT, 15min
    assert konfiguration.einstellungen.intervall_dauer == timedelta(minutes=15)


def test_alle_fehlenden_keys_stehen_in_einer_meldung():
    with pytest.raises(ValueError) as fehler:
        lies_konfiguration({"BITGET_API_KEY": "fake-key"})

    meldung = str(fehler.value)
    assert "BITGET_API_SECRET" in meldung
    assert "BITGET_PASSPHRASE" in meldung
    assert "BITGET_API_KEY" not in meldung  # ist ja gesetzt
    assert "fake-key" not in meldung


def test_leerer_key_gilt_als_fehlend():
    # z.B. «BITGET_API_SECRET=» in der .env, wenn man den Wert vergessen hat
    with pytest.raises(ValueError, match="BITGET_API_SECRET"):
        lies_konfiguration(KEYS | {"BITGET_API_SECRET": ""})


@pytest.mark.parametrize(
    "wert",
    [
        "geheim\r",  # .env mit Windows-Zeilenenden
        "geheim\n",
        " geheim",
        "geheim ",
    ],
)
def test_key_mit_leerzeichen_am_rand_wird_abgelehnt_ohne_den_wert_zu_zeigen(wert):
    # Bitget würde den Key ablehnen, und httpx zeigte ihn in der Fehlermeldung im Log.
    with pytest.raises(ValueError) as fehler:
        lies_konfiguration(KEYS | {"BITGET_PASSPHRASE": wert})

    assert "BITGET_PASSPHRASE" in str(fehler.value)
    assert "geheim" not in str(fehler.value)
