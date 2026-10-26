"""Unit-Tests für die Bollinger-Berechnung.

Alle Soll-Werte sind von Hand nachgerechnet (siehe Kommentar pro Test), nicht mit dem getesteten
Code oder einer Statistik-Bibliothek erzeugt.
"""

import pytest

from bollinger_bot.bollinger import berechne_baender


def test_beispiel_aus_dem_readme():
    # Kurse 10, 12, 14, n = 3, k = 2
    # Mitte: (10 + 12 + 14) / 3 = 12
    # Varianz: ((−2)² + 0² + 2²) / 3 = 8 / 3 = 2.6667, σ = √2.6667 = 1.63299
    # Bänder: 12 ± 2 · 1.63299 = 12 ± 3.26599
    baender = berechne_baender([10, 12, 14], n=3, k=2)

    assert baender.mitte == pytest.approx(12)
    assert baender.oben == pytest.approx(15.26599, abs=1e-5)
    assert baender.unten == pytest.approx(8.73401, abs=1e-5)


def test_sigma_wird_durch_n_geteilt_nicht_durch_n_minus_1():
    # Typischer Fehler: Varianz 8 / (3 − 1) = 4, σ = 2, Bänder bei 8.00 und 16.00
    baender = berechne_baender([10, 12, 14], n=3, k=2)

    assert baender.oben != pytest.approx(16.0, abs=0.01)
    assert baender.unten != pytest.approx(8.0, abs=0.01)


def test_standardwerte_n_20_und_k_2():
    # Kurse 1 bis 20. Mitte: (1 + 20) / 2 = 10.5
    # Varianz der Zahlen 1 bis N (geteilt durch N): (N² − 1) / 12 = 399 / 12 = 33.25
    # σ = √33.25 = 5.76628, Bänder: 10.5 ± 2 · 5.76628 = 10.5 ± 11.53256
    baender = berechne_baender(list(range(1, 21)))

    assert baender.mitte == pytest.approx(10.5)
    assert baender.oben == pytest.approx(22.03256, abs=1e-5)
    assert baender.unten == pytest.approx(-1.03256, abs=1e-5)


def test_anderes_k_verschiebt_nur_die_baender():
    # Wie im README-Beispiel, aber k = 1: 12 ± 1.63299
    baender = berechne_baender([10, 12, 14], n=3, k=1)

    assert baender.mitte == pytest.approx(12)
    assert baender.oben == pytest.approx(13.63299, abs=1e-5)
    assert baender.unten == pytest.approx(10.36701, abs=1e-5)


def test_nur_die_letzten_n_kurse_zaehlen():
    # Der Ausreisser 100 liegt vor den letzten drei Kursen und darf nichts ändern.
    baender = berechne_baender([100, 10, 12, 14], n=3, k=2)

    assert baender.mitte == pytest.approx(12)
    assert baender.unten == pytest.approx(8.73401, abs=1e-5)


def test_gleiche_kurse_ergeben_baender_auf_dem_mittelband():
    # Keine Streuung: σ = 0, alle drei Werte liegen bei 5
    baender = berechne_baender([5, 5, 5, 5], n=4)

    assert baender.mitte == pytest.approx(5)
    assert baender.oben == pytest.approx(5)
    assert baender.unten == pytest.approx(5)


def test_genau_n_kurse_reichen():
    # Grenzwert: n Kurse sind das Minimum. k = 2 ist Standard, also dieselbe Rechnung wie im
    # README-Beispiel: Mitte (10 + 12 + 14) / 3 = 12, Bänder 12 ± 2 · 1.63299 = 12 ± 3.26599
    baender = berechne_baender([10, 12, 14], n=3)

    assert baender.mitte == pytest.approx(12)
    assert baender.oben == pytest.approx(15.26599, abs=1e-5)
    assert baender.unten == pytest.approx(8.73401, abs=1e-5)


def test_ein_kurs_zu_wenig_wirft_fehler():
    # Grenzwert: n − 1 Kurse reichen nicht
    with pytest.raises(ValueError, match="Mindestens 3 Schlusskurse"):
        berechne_baender([10, 12], n=3)


@pytest.mark.parametrize("n", [1, 0, -5])
def test_n_unter_2_wirft_fehler(n):
    # Mit einem einzigen Kurs gibt es keine Streuung, also keine sinnvollen Bänder
    with pytest.raises(ValueError, match="n muss mindestens 2"):
        berechne_baender([10, 12, 14], n=n)


@pytest.mark.parametrize("k", [0, -2])
def test_k_nicht_positiv_wirft_fehler(k):
    # k = 0 legt beide Bänder aufs Mittelband, ein negatives k vertauscht oben und unten
    with pytest.raises(ValueError, match="k muss grösser als 0"):
        berechne_baender([10, 12, 14], n=3, k=k)
