"""Unit-Tests für den Risiko-Check.

Alle Soll-Werte sind von Hand nachgerechnet (Rechnung als Kommentar pro Test). Handelsregeln in
den meisten Tests: BTCUSDT Spot, Menge auf 6 Stellen, Beträge auf 2 Stellen, mindestens 1 USDT.
"""

import pytest

from bollinger_bot.risiko import (
    BTCUSDT_SPOT,
    Handelsregeln,
    abrunden,
    kaufbetrag,
    verkaufsmenge,
)

# --- Abrunden ---


@pytest.mark.parametrize(
    ("wert", "stellen", "erwartet"),
    [
        (0.1239, 2, 0.12),
        (0.129, 2, 0.12),  # immer abrunden: kaufmännisch gerundet wäre es 0.13
        (0.0012349, 6, 0.001234),  # 6 Stellen wie bei BTC
        (5.0, 2, 5.0),  # schon gerundet: bleibt gleich
        (7.9, 0, 7.0),  # 0 Stellen: ganze Zahl
    ],
)
def test_abrunden(wert, stellen, erwartet):
    assert abrunden(wert, stellen) == erwartet


def test_abrunden_ohne_float_fehler():
    # Mit math.floor(0.29 * 100) / 100 käme 0.28 heraus, weil 0.29 · 100 in float
    # 28.999999999999996 ergibt. Abrunden darf einen schon gerundeten Wert nicht verkleinern.
    assert abrunden(0.29, 2) == 0.29


def test_abrunden_kleiner_btc_menge():
    # 0.000001 BTC ist die kleinste Menge mit 6 Stellen, str(1e-06) ist "1e-06"
    assert abrunden(0.000001, 6) == 0.000001
    assert abrunden(0.0000009, 6) == 0.0


def test_abrunden_mit_negativen_stellen_wirft_fehler():
    with pytest.raises(ValueError, match="stellen darf nicht negativ"):
        abrunden(1.5, -1)


# --- Kaufbetrag ---


def test_kaufbetrag_ist_anteil_vom_guthaben():
    # 1000 USDT · 10 / 100 = 100 USDT
    assert kaufbetrag(guthaben=1000, anteil_prozent=10, regeln=BTCUSDT_SPOT) == 100.0


def test_kaufbetrag_wird_auf_rappen_abgerundet():
    # 123.4567 USDT · 10 / 100 = 12.34567, abgerundet 12.34
    assert kaufbetrag(guthaben=123.4567, anteil_prozent=10, regeln=BTCUSDT_SPOT) == 12.34


def test_kaufbetrag_mit_ganzem_guthaben():
    # Grenzwert: 100 % ist erlaubt, 50.999 USDT abgerundet 50.99
    assert kaufbetrag(guthaben=50.999, anteil_prozent=100, regeln=BTCUSDT_SPOT) == 50.99


def test_kaufbetrag_genau_auf_dem_doppelten_mindestbetrag():
    # Grenzwert: 20 USDT · 10 / 100 = 2.00 USDT = 2 · 1 USDT Mindestbetrag, reicht noch
    assert kaufbetrag(guthaben=20, anteil_prozent=10, regeln=BTCUSDT_SPOT) == 2.0


def test_kaufbetrag_knapp_unter_dem_doppelten_mindestbetrag_ist_null():
    # Grenzwert: 19.99 USDT · 10 / 100 = 1.999, abgerundet 1.99 < 2: keine Order
    assert kaufbetrag(guthaben=19.99, anteil_prozent=10, regeln=BTCUSDT_SPOT) == 0.0


def test_kauf_von_genau_dem_mindestbetrag_ist_zu_klein():
    # 1.00 USDT wäre bei Bitget erlaubt, liesse sich nach der Gebühr aber nicht mehr verkaufen
    # (Beispiel von Lucas im Review zu PR #8, siehe KAUF_PUFFER_FAKTOR)
    assert kaufbetrag(guthaben=1, anteil_prozent=100, regeln=BTCUSDT_SPOT) == 0.0


def test_kleinster_kauf_laesst_sich_nach_gebuehr_und_stop_loss_verkaufen():
    # Kleinster Kauf 2.00 USDT zu 100 000 USDT/BTC: 2 / 100 000 = 0.00002 BTC, nach 0.1 % Gebühr
    # 0.00001998 BTC. Kurs fällt um 3 % auf 97 000 (Stop-Loss): abgerundet 0.000019 BTC,
    # Wert 0.000019 · 97 000 = 1.843 USDT >= 1, der Verkauf geht noch.
    betrag = kaufbetrag(guthaben=20, anteil_prozent=10, regeln=BTCUSDT_SPOT)
    menge_nach_gebuehr = betrag / 100_000 * 0.999

    assert betrag == 2.0
    assert verkaufsmenge(bestand=menge_nach_gebuehr, kurs=97_000, regeln=BTCUSDT_SPOT) == 0.000019


def test_mindestbetrag_gilt_fuer_den_abgerundeten_betrag():
    # Mindestbetrag 1.0025 USDT, Kauf also erst ab 2.005 USDT. 2.009 liegt vor dem Runden
    # darüber, abgerundet 2.00 aber darunter: keine Order. Wer zuerst prüft und danach rundet,
    # würde 2.00 USDT an Bitget schicken. 2.019 wird zu 2.01 und reicht.
    regeln = Handelsregeln(mengen_stellen=6, betrag_stellen=2, mindestbetrag=1.0025)

    assert kaufbetrag(guthaben=2.019, anteil_prozent=100, regeln=regeln) == 2.01
    assert kaufbetrag(guthaben=2.009, anteil_prozent=100, regeln=regeln) == 0.0


def test_kaufbetrag_ohne_float_fehler():
    # In float ergibt 16.4 · 15 / 100 = 2.4599999999999995, abgerundet 2.45. Richtig ist 2.46.
    # (Lucas' Beispiel 8.2 · 15 % = 1.23 liegt unter dem Puffer, darum das Doppelte.)
    assert kaufbetrag(guthaben=16.4, anteil_prozent=15, regeln=BTCUSDT_SPOT) == 2.46


def test_kaufbetrag_ohne_guthaben_ist_null():
    assert kaufbetrag(guthaben=0, anteil_prozent=10, regeln=BTCUSDT_SPOT) == 0.0


def test_hoechstbetrag_begrenzt_den_kauf():
    # 10 000 USDT · 10 / 100 = 1000 USDT, begrenzt auf 250 USDT
    betrag = kaufbetrag(
        guthaben=10_000, anteil_prozent=10, regeln=BTCUSDT_SPOT, hoechstbetrag=250
    )
    assert betrag == 250.0


def test_hoechstbetrag_greift_nicht_wenn_anteil_kleiner_ist():
    # 1000 USDT · 10 / 100 = 100 USDT, liegt unter 250
    betrag = kaufbetrag(guthaben=1000, anteil_prozent=10, regeln=BTCUSDT_SPOT, hoechstbetrag=250)
    assert betrag == 100.0


@pytest.mark.parametrize("anteil_prozent", [0, -5, 100.01, 150])
def test_ungueltiger_anteil_wirft_fehler(anteil_prozent):
    with pytest.raises(ValueError, match="anteil_prozent muss grösser als 0"):
        kaufbetrag(guthaben=1000, anteil_prozent=anteil_prozent, regeln=BTCUSDT_SPOT)


def test_negatives_guthaben_wirft_fehler():
    with pytest.raises(ValueError, match="guthaben darf nicht negativ"):
        kaufbetrag(guthaben=-1, anteil_prozent=10, regeln=BTCUSDT_SPOT)


@pytest.mark.parametrize("hoechstbetrag", [0, -10])
def test_ungueltiger_hoechstbetrag_wirft_fehler(hoechstbetrag):
    with pytest.raises(ValueError, match="hoechstbetrag muss grösser als 0"):
        kaufbetrag(
            guthaben=1000, anteil_prozent=10, regeln=BTCUSDT_SPOT, hoechstbetrag=hoechstbetrag
        )


def test_kaufbetrag_nur_mit_namen():
    # Schutz gegen vertauschte Zahlen: guthaben und anteil_prozent sehen sich ähnlich
    with pytest.raises(TypeError):
        kaufbetrag(1000, 10, BTCUSDT_SPOT)  # type: ignore[misc]


# --- Verkaufsmenge ---


def test_verkaufsmenge_wird_auf_6_stellen_abgerundet():
    # Nach Abzug der Gebühr bleiben z.B. 0.0011987 BTC, Bitget nimmt nur 6 Stellen: 0.001198
    assert verkaufsmenge(bestand=0.0011987, kurs=85_000, regeln=BTCUSDT_SPOT) == 0.001198


def test_verkaufsmenge_genau_auf_dem_mindestbetrag():
    # Grenzwert: 0.00002 BTC · 50 000 USDT = 1.00 USDT, genau der Mindestbetrag
    assert verkaufsmenge(bestand=0.00002, kurs=50_000, regeln=BTCUSDT_SPOT) == 0.00002


def test_verkaufsmenge_knapp_unter_dem_mindestbetrag_ist_null():
    # Grenzwert: 0.00002 BTC · 49 999.99 USDT = 0.9999998 USDT < 1: Rest lässt sich nicht verkaufen
    assert verkaufsmenge(bestand=0.00002, kurs=49_999.99, regeln=BTCUSDT_SPOT) == 0.0


def test_mindestbetrag_gilt_fuer_die_abgerundete_menge():
    # Kurs 50 000: 0.0000209 BTC abgerundet 0.00002, Wert 0.00002 · 50 000 = 1.00 USDT: reicht.
    # Kurs 48 000: vor dem Runden 0.0000209 · 48 000 = 1.0032 USDT, nach dem Runden
    # 0.00002 · 48 000 = 0.96 USDT: reicht nicht, obwohl der ungerundete Wert über 1 liegt.
    assert verkaufsmenge(bestand=0.0000209, kurs=50_000, regeln=BTCUSDT_SPOT) == 0.00002
    assert verkaufsmenge(bestand=0.0000209, kurs=48_000, regeln=BTCUSDT_SPOT) == 0.0


def test_verkaufsmenge_ohne_bestand_ist_null():
    assert verkaufsmenge(bestand=0, kurs=85_000, regeln=BTCUSDT_SPOT) == 0.0


def test_negativer_bestand_wirft_fehler():
    with pytest.raises(ValueError, match="bestand darf nicht negativ"):
        verkaufsmenge(bestand=-0.001, kurs=85_000, regeln=BTCUSDT_SPOT)


@pytest.mark.parametrize("kurs", [0, -1])
def test_kurs_nicht_positiv_wirft_fehler(kurs):
    with pytest.raises(ValueError, match="kurs muss grösser als 0"):
        verkaufsmenge(bestand=0.001, kurs=kurs, regeln=BTCUSDT_SPOT)


def test_verkaufsmenge_nur_mit_namen():
    with pytest.raises(TypeError):
        verkaufsmenge(0.001, 85_000, BTCUSDT_SPOT)  # type: ignore[misc]
