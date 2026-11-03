"""Integrationstests für die Seite des Dashboards mit Streamlit `AppTest`.

`AppTest` führt `dashboard.py` aus wie `streamlit run`, nur ohne Browser. Danach lesen die Tests
Meldungen, Kennzahlen und Tabellen der Seite. Jeder Test füllt eine echte SQLite-Datei in einem
temporären Ordner über `Speicher`, in derselben Reihenfolge wie `tick()`, und zeigt sie dem
Dashboard über `BOT_DB_PFAD`.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from bollinger_bot import dashboard
from bollinger_bot.polling import letzte_abgeschlossene_kerze
from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade

MIN_15 = timedelta(minutes=15)
START = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)  # 08:00 Schweizer Zeit (Sommerzeit)


@pytest.fixture
def pfad(tmp_path, monkeypatch):
    pfad = tmp_path / "bot.db"
    monkeypatch.setenv("BOT_DB_PFAD", str(pfad))
    monkeypatch.delenv("BOT_INTERVALL", raising=False)  # Standard: 15min
    return pfad


def seite():
    # Der erste Durchlauf lädt Streamlit und Altair und braucht länger als die 3 s, die AppTest
    # sonst wartet.
    return AppTest.from_file(dashboard.__file__, default_timeout=30).run()


def kerze(*, startzeit, schlusskurs, signal="halten"):
    """Eine ausgewertete Kerze mit festen Bändern: unten 79'200, Mitte 80'000, oben 80'800."""
    return SignalEintrag(
        startzeit=startzeit,
        eroeffnung=schlusskurs,
        hoch=schlusskurs + 100,
        tief=schlusskurs - 100,
        schlusskurs=schlusskurs,
        mittelband=80_000.0,
        oberes_band=80_800.0,
        unteres_band=79_200.0,
        signal=signal,
    )


def speichere_wie_tick(speicher, *, startzeit, schlusskurs, frei, signal="halten", trade=None):
    """Speichert eine ausgewertete Kerze wie tick(): zuerst den Trade, dann Signal und Kontostand.

    Trade und Kontostand bekommen die Zeit am Ende der Kerze.
    """
    jetzt = startzeit + MIN_15
    if trade is not None:
        trade_seite, menge, preis, order_id = trade
        speicher.speichere_trade(
            Trade(zeit=jetzt, seite=trade_seite, menge=menge, preis=preis, order_id=order_id)
        )
    speicher.speichere_signal(kerze(startzeit=startzeit, schlusskurs=schlusskurs, signal=signal))
    speicher.speichere_kontostand(Kontostand(zeit=jetzt, waehrung="USDT", betrag=frei))


def fuelle_backtest_beispiel(pfad):
    """Acht Kerzen ab 24.10.2026 06:00 UTC mit einem Kauf, einem Verkauf und einem zweiten Kauf.

    Order-IDs wie im Backtest («sim-…»), die freien USDT von Hand nachgerechnet.
    """
    with Speicher(pfad) as speicher:
        kerzen = [
            (80_000.0, 1000.0, "halten", None),
            # Schluss 79'000 unter dem unteren Band: Kauf 0.001 BTC für 79 USDT, 1000 − 79 = 921
            (79_000.0, 921.0, "kaufen", ("kauf", 0.001, 79_000.0, "sim-1")),
            (79_500.0, 921.0, "halten", None),
            (79_800.0, 921.0, "halten", None),
            # Schluss 81'000 über dem Mittelband: Verkauf für 81 USDT, 921 + 81 = 1002
            (81_000.0, 1002.0, "verkaufen", ("verkauf", 0.001, 81_000.0, "sim-2")),
            (80_000.0, 1002.0, "halten", None),
            # Kauf 0.002 BTC für 158 USDT, 1002 − 158 = 844
            (79_000.0, 844.0, "kaufen", ("kauf", 0.002, 79_000.0, "sim-3")),
            (79_200.0, 844.0, "halten", None),
        ]
        for nummer, (schlusskurs, frei, signal, trade) in enumerate(kerzen):
            speichere_wie_tick(
                speicher,
                startzeit=START + nummer * MIN_15,
                schlusskurs=schlusskurs,
                frei=frei,
                signal=signal,
                trade=trade,
            )


def tabelle(at, spalte):
    """Die Tabelle der Seite, die eine Spalte mit diesem Namen hat, als pandas-DataFrame."""
    (gefunden,) = [df.value for df in at.dataframe if spalte in df.value.columns]
    return gefunden


def chart_daten(at, nummer):
    """Alle Datenzeilen eines Charts der Seite.

    AppTest kennt keine Charts, liefert aber die Beschreibung, die Streamlit an den Browser
    schickt (Vega-Lite als JSON). Darin stehen die Daten unter `data.values`.
    """
    zeilen = []

    def suche(knoten):
        if isinstance(knoten, dict):
            zeilen.extend(knoten.get("data", {}).get("values", []))
            for wert in knoten.values():
                suche(wert)
        elif isinstance(knoten, list):
            for wert in knoten:
                suche(wert)

    suche(json.loads(at.get("vega_lite_chart")[nummer].proto.spec))
    return zeilen


# --- Ohne Daten ---


def test_fehlende_datenbank_zeigt_einen_fehler_und_legt_nichts_an(tmp_path, monkeypatch):
    # Speicher() würde den Ordner und eine leere Datenbank anlegen. Das Dashboard darf das nicht,
    # sonst sähe ein vertippter BOT_DB_PFAD wie ein Bot ohne Daten aus.
    pfad = tmp_path / "data" / "bot.db"
    monkeypatch.setenv("BOT_DB_PFAD", str(pfad))

    at = seite()

    assert not at.exception
    assert "Keine Datenbank unter" in at.error[0].value
    assert not (tmp_path / "data").exists()


def test_leere_datenbank_zeigt_einen_hinweis(pfad):
    Speicher(pfad).schliessen()  # legt die leeren Tabellen an, wie der Bot beim ersten Start

    at = seite()

    assert not at.exception
    assert "Noch keine ausgewerteten Kerzen" in at.info[0].value
    assert not at.get("vega_lite_chart")


def test_ungueltiges_intervall_zeigt_einen_fehler(pfad, monkeypatch):
    monkeypatch.setenv("BOT_INTERVALL", "15m")  # Futures-Schreibweise

    at = seite()

    assert not at.exception
    assert "BOT_INTERVALL '15m' ist ungültig" in at.error[0].value


def test_kerze_ohne_kontostand_und_ohne_trades(pfad):
    # Stürzt tick() nach dem Signal und vor dem Kontostand ab, steht eine Kerze ohne Kontostand
    # in der Datenbank. Ohne Kontostand gibt es keinen Gesamtwert.
    with Speicher(pfad) as speicher:
        speicher.speichere_signal(kerze(startzeit=START, schlusskurs=80_000.0))

    at = seite()

    assert not at.exception
    assert at.metric[0].value == "–"
    assert len(at.get("vega_lite_chart")) == 1  # nur der Kurs
    assert "Keine Trades im gewählten Zeitraum." in [caption.value for caption in at.caption]


# --- Mit Daten ---


def test_kennzahlen_aus_den_daten_des_backtests(pfad):
    fuelle_backtest_beispiel(pfad)

    at = seite()

    assert not at.exception
    gesamt, frei, offen, kurs = at.metric
    # Gesamtwert: 844 freie USDT + 0.002 BTC · 79'200 = 844 + 158.40 = 1002.40.
    # Start: 1000 USDT ohne Position. 1002.40 − 1000 = +2.40, 2.40 / 1000 = +0.24 %
    assert gesamt.value == "1'002.40"
    assert gesamt.delta == "+2.40 (+0.24 %) seit Start"
    assert frei.value == "844.00"
    # Offene Position 0.002 BTC, gekauft zu 79'000: (79'200 − 79'000) · 0.002 = +0.40 USDT
    assert offen.value == "0.002000"
    assert offen.delta == "+0.40 USDT seit Kauf"
    assert kurs.value == "79'200.00"


def test_hinweis_demo_daten_und_warnung_bei_alten_kerzen(pfad):
    # Die letzte Kerze ist vom 24.10.2026, also längst mehr als zwei Intervalle her.
    fuelle_backtest_beispiel(pfad)

    at = seite()

    assert "Demo-Daten" in at.info[0].value
    assert "Keine neuen Kerzen" in at.warning[0].value
    # Ende der letzten Kerze: 07:45 + 15 min = 08:00 UTC = 10:00 Schweizer Zeit
    assert "endete am 24.10.2026 10:00" in at.warning[0].value


def test_trades_und_kerzen_als_tabelle_in_schweizer_zeit(pfad):
    fuelle_backtest_beispiel(pfad)

    at = seite()

    trades = tabelle(at, "Order-ID")
    assert list(trades["Order-ID"]) == ["sim-3", "sim-2", "sim-1"]  # neueste zuerst
    # Kauf sim-3 nach der Kerze ab 07:30 UTC, also um 07:45 UTC = 09:45 Schweizer Zeit
    assert trades["Zeit"][0] == "24.10.2026 09:45"
    assert len(tabelle(at, "Kerze ab")) == 8


def test_kaeufe_und_verkaeufe_erscheinen_im_kurs_chart(pfad):
    fuelle_backtest_beispiel(pfad)

    at = seite()

    assert len(at.get("vega_lite_chart")) == 2  # Kurs und Gesamtwert
    markierungen = [zeile for zeile in chart_daten(at, 0) if "order_id" in zeile]
    assert [(zeile["seite"], zeile["order_id"]) for zeile in markierungen] == [
        ("Kauf", "sim-1"),
        ("Verkauf", "sim-2"),
        ("Kauf", "sim-3"),
    ]


def test_aktuelle_daten_ohne_warnung_und_ohne_demo_hinweis(pfad):
    # Wie im laufenden Betrieb: Die letzte Kerze ist eben fertig geworden, die Order-ID kommt
    # von Bitget.
    startzeit = letzte_abgeschlossene_kerze(datetime.now(UTC), MIN_15)
    with Speicher(pfad) as speicher:
        speichere_wie_tick(
            speicher,
            startzeit=startzeit,
            schlusskurs=79_000.0,
            frei=921.0,
            signal="kaufen",
            trade=("kauf", 0.001, 79_000.0, "1098394147267141632"),
        )

    at = seite()

    assert not at.exception
    assert not at.warning
    assert not at.info


def test_zeitraum_gilt_fuer_die_tabellen(pfad):
    # 120 Kerzen à 15 min = 30 Stunden. Kauf nach Kerze 2 (vor dem letzten Tag), Verkauf nach
    # Kerze 110 (im letzten Tag).
    with Speicher(pfad) as speicher:
        for nummer in range(120):
            trade = None
            if nummer == 2:
                trade = ("kauf", 0.001, 80_000.0, "sim-1")
            if nummer == 110:
                trade = ("verkauf", 0.001, 80_000.0, "sim-2")
            speichere_wie_tick(
                speicher,
                startzeit=START + nummer * MIN_15,
                schlusskurs=80_000.0,
                frei=1000.0,
                trade=trade,
            )

    at = seite()

    # Standard «1 Tag»: 24 · 4 = 96 Kerzen, nur der Verkauf liegt darin
    assert at.radio[0].value == "1 Tag"
    assert len(tabelle(at, "Kerze ab")) == 96
    assert list(tabelle(at, "Order-ID")["Order-ID"]) == ["sim-2"]
    assert at.metric[2].value == "keine"  # verkauft, keine offene Position

    at.radio[0].set_value("6 Stunden").run()
    assert len(tabelle(at, "Kerze ab")) == 6 * 4

    at.radio[0].set_value("Alles").run()
    assert len(tabelle(at, "Kerze ab")) == 120
    assert list(tabelle(at, "Order-ID")["Order-ID"]) == ["sim-2", "sim-1"]


def test_dashboard_schreibt_nie_in_die_datenbank(pfad):
    fuelle_backtest_beispiel(pfad)
    vorher = pfad.read_bytes()
    dateien_vorher = sorted(datei.name for datei in pfad.parent.iterdir())

    at = seite()
    at.radio[0].set_value("Alles").run()  # ein zweiter Durchlauf mit eigenem Speicher

    assert not at.exception
    assert pfad.read_bytes() == vorher
    assert sorted(datei.name for datei in pfad.parent.iterdir()) == dateien_vorher
