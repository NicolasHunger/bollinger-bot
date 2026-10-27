"""Unit-Tests: Der BitgetClient prüft Keys und Timeout schon beim Erstellen.

Es geht keine Anfrage an Bitget: Der Konstruktor legt nur den HTTP-Client an.
"""

import pytest

from bollinger_bot.bitget_client import BitgetClient

GUT = {"api_key": "fake-key", "api_secret": "fake-secret", "passphrase": "fake-pass"}


def test_gueltige_keys_werden_angenommen():
    BitgetClient(**GUT).schliessen()


@pytest.mark.parametrize("name", ["api_key", "api_secret", "passphrase"])
@pytest.mark.parametrize(
    "schlechter_wert",
    [
        "",
        "GEHEIM-wert\n",  # .env mit Zeilenumbruch am Ende
        "GEHEIM-wert\r",  # .env mit Windows-Zeilenenden
        " GEHEIM-wert",  # Leerzeichen am Anfang
        "GEHEIM\twert",  # Tabulator in der Mitte
    ],
)
def test_ungueltige_keys_werden_abgelehnt_ohne_den_wert_zu_nennen(name, schlechter_wert):
    with pytest.raises(ValueError, match=name) as fehler:
        BitgetClient(**{**GUT, name: schlechter_wert})

    # Der Wert darf nicht in der Meldung stehen, sonst landet er im Log.
    assert "GEHEIM" not in str(fehler.value)


@pytest.mark.parametrize("timeout", [None, 0, -1])
def test_timeout_laesst_sich_nicht_abschalten(timeout):
    # Ohne Timeout liesse ein hängender Request den Bot ohne Fehlermeldung stillstehen.
    with pytest.raises(ValueError, match="timeout_sekunden muss grösser als 0"):
        BitgetClient(**GUT, timeout_sekunden=timeout)


def test_kleiner_timeout_ist_erlaubt():
    # Grenzwert: knapp über 0 ist erlaubt
    BitgetClient(**GUT, timeout_sekunden=0.1).schliessen()
