"""Unit-Tests für die Kauf- und Verkaufsregeln.

Beispiel-Bänder in allen Tests: unteres Band 90, Mittelband 100. Das obere Band spielt für die
Regeln keine Rolle. Kaufpreis 100 ergibt mit 3 % Stop-Loss einen Stop-Loss-Kurs von 97.
"""

import pytest

from bollinger_bot.regeln import Signal, entscheide

UNTEN = 90
MITTE = 100


def ohne_position(schlusskurs):
    return entscheide(schlusskurs=schlusskurs, unteres_band=UNTEN, mittelband=MITTE, kaufpreis=None)


def mit_position(schlusskurs, kaufpreis=100, stop_loss_prozent=3.0):
    return entscheide(
        schlusskurs=schlusskurs,
        unteres_band=UNTEN,
        mittelband=MITTE,
        kaufpreis=kaufpreis,
        stop_loss_prozent=stop_loss_prozent,
    )


# --- Ohne offene Position: kaufen oder warten ---


def test_kauft_wenn_kerze_unter_dem_unteren_band_schliesst():
    assert ohne_position(89) == Signal.KAUFEN


def test_kauft_nicht_wenn_kerze_genau_auf_dem_unteren_band_schliesst():
    # Grenzwert: «unter dem Band» heisst echt kleiner
    assert ohne_position(90) == Signal.HALTEN


def test_kauft_knapp_unter_dem_unteren_band():
    # Grenzwert: knapp darunter reicht
    assert ohne_position(89.99) == Signal.KAUFEN


def test_wartet_zwischen_den_baendern():
    assert ohne_position(95) == Signal.HALTEN


def test_verkauft_nichts_ohne_position_auch_ueber_dem_mittelband():
    # Ohne Position gibt es nichts zu verkaufen
    assert ohne_position(105) == Signal.HALTEN


# --- Mit offener Position: Verkauf am Mittelband ---


def test_verkauft_wenn_kerze_ueber_dem_mittelband_schliesst():
    assert mit_position(101) == Signal.VERKAUFEN


def test_haelt_wenn_kerze_genau_auf_dem_mittelband_schliesst():
    # Grenzwert: «über dem Mittelband» heisst echt grösser
    assert mit_position(100) == Signal.HALTEN


def test_haelt_zwischen_stop_loss_und_mittelband():
    assert mit_position(98) == Signal.HALTEN


def test_kauft_nicht_nach_wenn_kurs_wieder_unter_das_untere_band_faellt():
    # Kaufpreis 92, Stop-Loss-Kurs 92 · 97 / 100 = 89.24. Kurs 89.5 liegt unter dem unteren Band,
    # aber über dem Stop-Loss: kein zweiter Kauf, kein Verkauf.
    assert mit_position(89.5, kaufpreis=92) == Signal.HALTEN


# --- Mit offener Position: Stop-Loss ---


def test_stop_loss_verkauft_mehr_als_3_prozent_unter_kaufpreis():
    # Kaufpreis 100, Stop-Loss-Kurs 97. Kurs 96 liegt 4 % darunter.
    assert mit_position(96) == Signal.VERKAUFEN


def test_stop_loss_haelt_bei_genau_3_prozent():
    # Grenzwert: genau 3 % Verlust (Kurs 97) löst noch nicht aus
    assert mit_position(97) == Signal.HALTEN


def test_stop_loss_verkauft_knapp_unter_3_prozent():
    # Grenzwert: 96.99 liegt knapp unter dem Stop-Loss-Kurs 97
    assert mit_position(96.99) == Signal.VERKAUFEN


@pytest.mark.parametrize(("schlusskurs", "erwartet"), [(58_200, Signal.HALTEN), (58_199.99, Signal.VERKAUFEN)])
def test_stop_loss_mit_realistischem_bitcoin_kurs(schlusskurs, erwartet):
    # Kaufpreis 60 000, Stop-Loss-Kurs 60 000 · 97 / 100 = 58 200. Die Bänder müssen zum Kurs
    # passen, sonst greift statt des Stop-Loss die Regel «über dem Mittelband».
    signal = entscheide(
        schlusskurs=schlusskurs, unteres_band=59_000, mittelband=61_000, kaufpreis=60_000
    )
    assert signal == erwartet


def test_stop_loss_prozent_ist_einstellbar():
    # 5 % statt 3 %: Stop-Loss-Kurs 95, Kurs 96 hält jetzt
    assert mit_position(96, stop_loss_prozent=5) == Signal.HALTEN
    assert mit_position(94.99, stop_loss_prozent=5) == Signal.VERKAUFEN


@pytest.mark.parametrize("stop_loss_prozent", [0, -1, 100, 150])
def test_ungueltiger_stop_loss_wirft_fehler(stop_loss_prozent):
    with pytest.raises(ValueError, match="stop_loss_prozent muss zwischen 0 und 100"):
        mit_position(98, stop_loss_prozent=stop_loss_prozent)


def test_argumente_muessen_mit_namen_uebergeben_werden():
    # Schutz gegen vertauschte Kurse: Aufruf ohne Namen ist nicht erlaubt
    with pytest.raises(TypeError):
        entscheide(89, 90, 100, None)  # type: ignore[misc]
