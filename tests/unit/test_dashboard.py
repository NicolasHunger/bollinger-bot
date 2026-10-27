"""Unit-Tests für die Rechnungen des Dashboards: Konfiguration, Schweizer Zeit, «veraltet?»,
Gesamtwert und Zeitraum.

Alles reine Funktionen: Kein Test öffnet eine Datei oder startet Streamlit. Die Seite selbst
testet `tests/integration/test_dashboard.py`.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bollinger_bot.dashboard import (
    INTERVALLE,
    Wertpunkt,
    als_schweizer_zeit,
    als_zahl,
    gesamtwerte,
    ist_veraltet,
    kerzen_zeilen,
    lies_konfiguration,
    sind_demo_daten,
    zeit_als_text,
    zeit_fuer_chart,
    zeitraum_beginn,
)
from bollinger_bot.speicher import Kontostand, SignalEintrag, Trade

MIN_1 = timedelta(minutes=1)
MIN_15 = timedelta(minutes=15)


def utc(stunde, minute, sekunde=0, tag=24):
    return datetime(2026, 10, tag, stunde, minute, sekunde, tzinfo=UTC)


def signal(startzeit, schlusskurs):
    # Nur Startzeit und Schlusskurs zählen für den Gesamtwert, der Rest ist eine gültige Kerze.
    return SignalEintrag(
        startzeit=startzeit,
        eroeffnung=schlusskurs,
        hoch=schlusskurs,
        tief=schlusskurs,
        schlusskurs=schlusskurs,
        mittelband=schlusskurs,
        oberes_band=schlusskurs,
        unteres_band=schlusskurs,
        signal="halten",
    )


def trade(zeit, seite, *, menge=1.0, preis=100.0, order_id="1001"):
    return Trade(zeit=zeit, seite=seite, menge=menge, preis=preis, order_id=order_id)


def usdt(zeit, betrag):
    return Kontostand(zeit=zeit, waehrung="USDT", betrag=betrag)


# --- Konfiguration ---


def test_konfiguration_ohne_umgebungsvariablen_wie_der_bot():
    konfiguration = lies_konfiguration({})

    assert konfiguration.db_pfad == Path("data/bot.db")
    assert konfiguration.intervall == "15min"
    assert konfiguration.intervall_dauer == MIN_15


def test_konfiguration_aus_umgebungsvariablen():
    konfiguration = lies_konfiguration({"BOT_DB_PFAD": "/data/bot.db", "BOT_INTERVALL": "1h"})

    assert konfiguration.db_pfad == Path("/data/bot.db")
    assert konfiguration.intervall == "1h"
    assert konfiguration.intervall_dauer == timedelta(hours=1)


def test_leere_umgebungsvariablen_gelten_als_nicht_gesetzt():
    # Docker Compose setzt eine Variable ohne Wert als leeren Text.
    konfiguration = lies_konfiguration({"BOT_DB_PFAD": "", "BOT_INTERVALL": ""})

    assert konfiguration.db_pfad == Path("data/bot.db")
    assert konfiguration.intervall == "15min"


def test_ungueltiges_intervall_nennt_die_erlaubten():
    # Futures-Schreibweise "15m" statt "15min"
    with pytest.raises(ValueError, match="'15m' ist ungültig, erlaubt: 1min, 5min, 15min"):
        lies_konfiguration({"BOT_INTERVALL": "15m"})


def test_intervalle_wie_im_bitget_client():
    # Die Tabelle steht bis zum Merge von PR #9 zweimal im Code. Ohne bitget_client.py (noch
    # nicht auf main) wird der Test übersprungen.
    bitget_client = pytest.importorskip("bollinger_bot.bitget_client")

    assert INTERVALLE == bitget_client.INTERVALLE


# --- Schweizer Zeit ---


@pytest.mark.parametrize(
    ("zeit", "erwartet"),
    [
        # Sommerzeit (MESZ, UTC+2): 14:30 + 2 h = 16:30
        (utc(14, 30), "2026-10-24T16:30:00+02:00"),
        # Grenzwert: Am So 25.10.2026 um 01:00 UTC (03:00 Sommerzeit) wird die Uhr auf 02:00
        # zurückgestellt. 1 s vorher gilt noch UTC+2: 00:59:59 + 2 h = 02:59:59
        (utc(0, 59, 59, tag=25), "2026-10-25T02:59:59+02:00"),
        # genau auf der Umstellung gilt UTC+1: 01:00 + 1 h = 02:00
        (utc(1, 0, tag=25), "2026-10-25T02:00:00+01:00"),
        # Winterzeit (MEZ, UTC+1): 12:00 + 1 h = 13:00. Fest +2 h ergäbe falsch 14:00.
        (utc(12, 0, tag=26), "2026-10-26T13:00:00+01:00"),
    ],
)
def test_schweizer_zeit_mit_sommer_und_winterzeit(zeit, erwartet):
    assert als_schweizer_zeit(zeit).isoformat() == erwartet


def test_schweizer_zeit_ohne_zeitzone_ist_ein_fehler():
    # Ohne Zeitzone wüsste niemand, ob 14:30 in UTC oder schon in Schweizer Zeit gemeint ist.
    with pytest.raises(ValueError, match="ohne Zeitzone"):
        als_schweizer_zeit(datetime(2026, 10, 24, 14, 30))


def test_zeit_als_text():
    assert zeit_als_text(utc(14, 30)) == "24.10.2026 16:30"


def test_zeit_fuer_chart_gibt_die_schweizer_uhrzeit_als_utc_weiter():
    # 14:30:45.5 UTC = 16:30:45.5 Sommerzeit. Der Chart soll genau «16:30:45» zeigen, darum
    # Endung Z, und die Bruchteile von Sekunden fallen weg.
    zeit = datetime(2026, 10, 24, 14, 30, 45, 500000, tzinfo=UTC)

    assert zeit_fuer_chart(zeit) == "2026-10-24T16:30:45Z"


@pytest.mark.parametrize(
    ("wert", "optionen", "erwartet"),
    [
        (80257.5, {}, "80'257.50"),
        (993.0, {}, "993.00"),
        (1234567.891, {}, "1'234'567.89"),  # gerundet auf 2 Stellen
        (-7.0, {"vorzeichen": True}, "-7.00"),
        (7.0, {"vorzeichen": True}, "+7.00"),
        (0.001234764, {"stellen": 6}, "0.001235"),  # BTC: 6 Stellen wie bei Bitget
    ],
)
def test_als_zahl(wert, optionen, erwartet):
    assert als_zahl(wert, **optionen) == erwartet


# --- Veraltet? ---


@pytest.mark.parametrize(
    ("jetzt", "erwartet"),
    [
        # Kerze ab 12:00 endet um 12:15. Normalfall: Der Bot hat sie um 12:16 ausgewertet.
        (utc(12, 16), False),
        # Kurz vor der Auswertung der nächsten Kerze (ab 12:15, fertig um 12:30): Das Ende der
        # Kerze ab 12:00 liegt 15 min + 59 s zurück, das ist normal.
        (utc(12, 30, 59), False),
        # Grenzwert: genau zwei Intervalle nach dem Ende, 12:15 + 2 · 15 min = 12:45
        (utc(12, 45), False),
        # knapp darüber: 1 s mehr als zwei Intervalle
        (utc(12, 45, 1), True),
        # Bot steht seit Stunden
        (utc(18, 0), True),
    ],
)
def test_veraltet_ab_zwei_intervallen_nach_dem_ende_der_kerze(jetzt, erwartet):
    assert ist_veraltet(letzte_startzeit=utc(12, 0), jetzt=jetzt, intervall=MIN_15) is erwartet


def test_veraltet_bei_1_minuten_kerzen():
    # Kerze ab 12:00 endet um 12:01, zwei Intervalle später ist es 12:03.
    assert not ist_veraltet(letzte_startzeit=utc(12, 0), jetzt=utc(12, 3), intervall=MIN_1)
    assert ist_veraltet(letzte_startzeit=utc(12, 0), jetzt=utc(12, 3, 1), intervall=MIN_1)


# --- Demo-Daten? ---


def test_trades_aus_dem_backtest_sind_demo_daten():
    trades = [trade(utc(12, 15), "kauf", order_id="sim-1")]

    assert sind_demo_daten(trades)


def test_trades_von_bitget_sind_keine_demo_daten():
    trades = [
        trade(utc(12, 15), "kauf", order_id="1098394147267141632"),
        trade(utc(13, 0), "verkauf", order_id="1098394147267141633"),
    ]

    assert not sind_demo_daten(trades)


def test_ohne_trades_keine_demo_daten():
    assert not sind_demo_daten([])


# --- Gesamtwert ---


def test_gesamtwert_zaehlt_die_offene_position_zum_letzten_schlusskurs():
    # 15-Minuten-Kerzen. Die Kerze ab 12:00 schliesst bei 100, der Bot kauft um 12:15 für
    # 100 USDT 1 BTC, die freien USDT sinken von 1000 auf 900. Um 13:00 verkauft er 1 BTC zu 120.
    signale = [
        signal(utc(12, 0), 100.0),
        signal(utc(12, 15), 110.0),
        signal(utc(12, 30), 90.0),
        signal(utc(12, 45), 120.0),
    ]
    trades = [
        trade(utc(12, 15), "kauf", menge=1.0, preis=100.0),
        trade(utc(13, 0), "verkauf", menge=1.0, preis=120.0),
    ]
    kontostaende = [
        usdt(utc(12, 15), 900.0),
        usdt(utc(12, 30), 900.0),
        usdt(utc(12, 45), 900.0),
        usdt(utc(13, 0), 1020.0),  # 900 + 1 BTC · 120
    ]

    werte = gesamtwerte(
        kontostaende=kontostaende, trades=trades, signale=signale, intervall=MIN_15
    )

    assert werte == [
        # Kauf um 12:15 zählt schon (gleiche Zeit), fertig ist die Kerze ab 12:00:
        # 900 + 1 · 100 = 1000. Gekauft, nichts verloren.
        Wertpunkt(zeit=utc(12, 15), frei=900.0, position=100.0, gesamt=1000.0),
        # Kerze ab 12:15 fertig: 900 + 1 · 110 = 1010
        Wertpunkt(zeit=utc(12, 30), frei=900.0, position=110.0, gesamt=1010.0),
        # Kerze ab 12:30 fertig: 900 + 1 · 90 = 990
        Wertpunkt(zeit=utc(12, 45), frei=900.0, position=90.0, gesamt=990.0),
        # verkauft: keine Position mehr, 1020 + 0 = 1020
        Wertpunkt(zeit=utc(13, 0), frei=1020.0, position=0.0, gesamt=1020.0),
    ]


def test_kerze_die_beim_kontostand_noch_lief_zaehlt_nicht():
    # Im echten Betrieb speichert tick() den Kontostand um 12:30:42, kurz nach dem Ende der Kerze
    # ab 12:15. Die Kerze ab 12:30 hat da schon begonnen, ist aber erst um 12:45 fertig und
    # erst danach gespeichert. Ihr Schlusskurs 90 darf nicht zählen, sondern 110.
    signale = [signal(utc(12, 15), 110.0), signal(utc(12, 30), 90.0)]
    trades = [trade(utc(12, 15, 40), "kauf", menge=0.5, preis=100.0)]
    kontostaende = [usdt(utc(12, 30, 42), 950.0)]

    (wert,) = gesamtwerte(
        kontostaende=kontostaende, trades=trades, signale=signale, intervall=MIN_15
    )

    # 950 + 0.5 · 110 = 950 + 55 = 1005
    assert wert.gesamt == 1005.0


def test_ohne_trades_ist_der_gesamtwert_das_freie_guthaben():
    signale = [signal(utc(12, 0), 100.0)]

    (wert,) = gesamtwerte(
        kontostaende=[usdt(utc(12, 15), 1000.0)], trades=[], signale=signale, intervall=MIN_15
    )

    assert wert == Wertpunkt(zeit=utc(12, 15), frei=1000.0, position=0.0, gesamt=1000.0)


def test_ohne_fertige_kerze_zaehlt_der_kaufpreis():
    # Kommt bei tick() nicht vor (der Kontostand kommt nach der Kerze), aber ohne Kerze gäbe es
    # sonst keinen Kurs. Kauf 2 BTC zu 100, die erste Kerze endet erst um 12:15.
    signale = [signal(utc(12, 0), 130.0)]
    trades = [trade(utc(12, 10), "kauf", menge=2.0, preis=100.0)]

    (wert,) = gesamtwerte(
        kontostaende=[usdt(utc(12, 10), 800.0)], trades=trades, signale=signale, intervall=MIN_15
    )

    # 800 + 2 · 100 = 1000
    assert wert.gesamt == 1000.0


def test_kontostaende_in_anderen_waehrungen_zaehlen_nicht():
    kontostaende = [
        usdt(utc(12, 15), 1000.0),
        Kontostand(zeit=utc(12, 15), waehrung="BTC", betrag=0.5),
    ]

    werte = gesamtwerte(
        kontostaende=kontostaende, trades=[], signale=[signal(utc(12, 0), 100.0)], intervall=MIN_15
    )

    assert [wert.gesamt for wert in werte] == [1000.0]


def test_nachgetragener_trade_zaehlt_nach_seiner_zeit():
    # Ein Kauf um 12:15 wurde nach einem Absturz von Hand nachgetragen, nach dem Verkauf um
    # 13:00. hole_trades() liefert ihn darum als letzten, zählen muss er nach seiner Zeit.
    signale = [signal(utc(12, 0), 100.0), signal(utc(12, 45), 120.0)]
    trades = [
        trade(utc(13, 0), "verkauf", menge=1.0, preis=120.0, order_id="1002"),
        trade(utc(12, 15), "kauf", menge=1.0, preis=100.0, order_id="1001"),
    ]
    kontostaende = [usdt(utc(12, 15), 900.0), usdt(utc(13, 0), 1020.0)]

    werte = gesamtwerte(
        kontostaende=kontostaende, trades=trades, signale=signale, intervall=MIN_15
    )

    # 12:15: 900 + 1 · 100 = 1000. 13:00: verkauft, 1020 + 0 = 1020
    assert [wert.gesamt for wert in werte] == [1000.0, 1020.0]


# --- Zeitraum und Chart-Zeilen ---


def test_zeitraum_von_einem_tag_endet_mit_der_letzten_kerze():
    # Letzte Kerze ab 14:30, fertig um 14:45. Ein Tag davor: 23.10. 14:45. Bei 15-Minuten-Kerzen
    # sind das 24 · 4 = 96 Kerzen (23.10. 14:45 bis 24.10. 14:30).
    beginn = zeitraum_beginn(
        letzte_startzeit=utc(14, 30), intervall=MIN_15, dauer=timedelta(days=1)
    )

    assert beginn == utc(14, 45, tag=23)


def test_zeitraum_alles():
    assert (
        zeitraum_beginn(letzte_startzeit=utc(14, 30), intervall=MIN_15, dauer=None) is None
    )


def test_kerzen_zeile_fuer_den_chart():
    # Kerze ab 14:30 UTC = 16:30 Sommerzeit. Ein Zehntel von 15 min Abstand zu den Nachbarn:
    # Körper von 16:31:30 bis 16:43:30, Mitte bei 16:37:30.
    eintrag = SignalEintrag(
        startzeit=utc(14, 30),
        eroeffnung=100.0,
        hoch=104.0,
        tief=98.0,
        schlusskurs=103.0,
        mittelband=101.0,
        oberes_band=106.0,
        unteres_band=96.0,
        signal="halten",
    )

    (zeile,) = kerzen_zeilen([eintrag], intervall=MIN_15)

    assert zeile["von"] == "2026-10-24T16:31:30Z"
    assert zeile["mitte"] == "2026-10-24T16:37:30Z"
    assert zeile["bis"] == "2026-10-24T16:43:30Z"
    assert zeile["zeit"] == "24.10.2026 16:30"
    assert zeile["steigend"]  # Schluss 103 über der Eröffnung 100
