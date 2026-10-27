"""Dashboard: zeigt im Browser, was der Bot gemacht hat.

Start: `streamlit run src/bollinger_bot/dashboard.py`, danach http://localhost:8501 öffnen. Das
Dashboard liest die SQLite-Datei, die `tick()` schreibt (Pfad aus `BOT_DB_PFAD`), und zeigt:

- den Kurs als Kerzen mit den drei Bollinger-Bändern und den Käufen und Verkäufen,
- den Gesamtwert des Kontos: freie USDT plus die offene Position zum letzten Schlusskurs,
- eine Warnung, wenn die letzte ausgewertete Kerze zu alt ist (dann steht der Bot vermutlich),
- den Hinweis «Demo-Daten», wenn die Trades aus dem Backtest stammen.

Das Dashboard liest nur, es schreibt nie. Streamlit führt dieses Skript bei jedem Aufruf und jedem
Klick von oben bis unten neu aus, und zwar in wechselnden Threads. Eine sqlite3-Verbindung darf
aber nur der Thread benutzen, der sie geöffnet hat. Darum öffnet jeder Durchlauf die Datei mit
einem eigenen `with Speicher(pfad)` und schliesst sie gleich wieder.

Die Rechnungen (Gesamtwert, «veraltet?», Schweizer Zeit) sind reine Funktionen ohne Streamlit und
ohne Datei, getestet in `tests/unit/test_dashboard.py`. Die Seite selbst testet
`tests/integration/test_dashboard.py` mit Streamlit `AppTest` gegen eine echte SQLite-Datei.
"""

import os
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Altair zeichnet die Charts. Es kommt mit Streamlit mit (st.altair_chart braucht es), darum
# steht es nicht als eigene Abhängigkeit in der pyproject.toml.
import altair as alt
import streamlit as st

from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade

# Dauer einer Kerze je Intervall im Bitget-Format. Dieselbe Tabelle steht in
# `bitget_client.INTERVALLE` (PR #9). Bis PR #9 gemergt ist, steht sie hier ein zweites Mal,
# danach von dort importieren. Bis dahin prüft ein Unit-Test, dass beide gleich sind.
INTERVALLE = {
    "1min": timedelta(minutes=1),
    "5min": timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "30min": timedelta(minutes=30),
    "1h": timedelta(hours=1),
}

SCHWEIZ = ZoneInfo("Europe/Zurich")
QUOTEWAEHRUNG = "USDT"  # womit der Bot bezahlt, in dieser Währung speichert tick() den Kontostand
SEITEN = {"kauf": "Kauf", "verkauf": "Verkauf"}

# Wählbare Zeiträume für Charts und Tabellen, None heisst alles.
ZEITRAEUME = {
    "6 Stunden": timedelta(hours=6),
    "1 Tag": timedelta(days=1),
    "3 Tage": timedelta(days=3),
    "1 Woche": timedelta(weeks=1),
    "Alles": None,
}


# --- Konfiguration ---


@dataclass(frozen=True)
class Konfiguration:
    """Was das Dashboard aus den Umgebungsvariablen braucht. Keine Keys: Es fragt Bitget nie."""

    db_pfad: Path
    intervall: str  # Kerzen-Intervall im Bitget-Format, z.B. "15min"
    intervall_dauer: timedelta


def lies_konfiguration(umgebung: Mapping[str, str]) -> Konfiguration:
    """Liest `BOT_DB_PFAD` und `BOT_INTERVALL`, mit denselben Standardwerten wie der Bot."""
    # «or»: Ein leerer Wert (z.B. «BOT_INTERVALL=» oder aus Docker Compose) gilt als nicht gesetzt.
    intervall = umgebung.get("BOT_INTERVALL") or "15min"
    if intervall not in INTERVALLE:
        erlaubt = ", ".join(INTERVALLE)
        raise ValueError(f"BOT_INTERVALL {intervall!r} ist ungültig, erlaubt: {erlaubt}")
    return Konfiguration(
        db_pfad=Path(umgebung.get("BOT_DB_PFAD") or "data/bot.db"),
        intervall=intervall,
        intervall_dauer=INTERVALLE[intervall],
    )


# --- Zeiten und Zahlen für die Anzeige ---


def als_schweizer_zeit(zeit: datetime) -> datetime:
    """Rechnet eine Zeit (intern in UTC) für die Anzeige in Schweizer Zeit um.

    ZoneInfo kennt Sommer- und Winterzeit: Im Sommer gilt UTC+2, im Winter UTC+1. Fest zwei
    Stunden dazuzuzählen wäre ab der Umstellung am letzten Sonntag im Oktober falsch.
    """
    if zeit.tzinfo is None:
        raise ValueError(
            f"Zeit ohne Zeitzone: {zeit}. Bitte mit Zeitzone angeben, z.B. tzinfo=UTC."
        )
    return zeit.astimezone(SCHWEIZ)


def zeit_als_text(zeit: datetime) -> str:
    """Schweizer Zeit zum Lesen, z.B. «24.10.2026 16:30»."""
    return als_schweizer_zeit(zeit).strftime("%d.%m.%Y %H:%M")


def zeit_fuer_chart(zeit: datetime) -> str:
    """Schweizer Uhrzeit für die Charts, z.B. «2026-10-24T16:30:00Z».

    Die Charts zeichnet Vega-Lite im Browser, und Vega-Lite kennt nur zwei Zeitzonen: UTC und
    die des Browsers. Darum geben wir die Uhrzeit, die ZoneInfo für die Schweiz berechnet hat, als
    «UTC» weiter (Endung Z), und die Zeitachsen der Charts zeigen UTC. So erscheint genau diese
    Uhrzeit, egal in welcher Zeitzone der Browser läuft. Einziger Nachteil: In der Nacht der
    Umstellung im Herbst gibt es 02:00 bis 03:00 zweimal, die Kerzen dieser Stunde liegen im Chart
    übereinander.
    """
    return als_schweizer_zeit(zeit).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def als_zahl(wert: float, *, stellen: int = 2, vorzeichen: bool = False) -> str:
    """Zahl mit Apostroph als Tausendertrennzeichen wie in der Schweiz, z.B. «80'257.50»."""
    plus = "+" if vorzeichen else ""
    return f"{wert:{plus},.{stellen}f}".replace(",", "'")


# --- Rechnungen ---


def ist_veraltet(*, letzte_startzeit: datetime, jetzt: datetime, intervall: timedelta) -> bool:
    """True, wenn die letzte ausgewertete Kerze schon seit mehr als zwei Intervallen fertig ist.

    Normalerweise wertet der Bot eine Kerze in der ersten Minute nach ihrem Ende aus. Ihr Ende
    liegt also höchstens ein Intervall und eine Minute zurück, bis die nächste Kerze ausgewertet
    ist. Mehr als zwei Intervalle heisst: Mindestens eine Kerze wurde nicht ausgewertet. Vermutlich
    steht der Bot, oder Bitget lehnt jede Anfrage ab (z.B. ungültiger API-Key).

    Gemessen ab dem Ende der Kerze, nicht ab ihrem Start: Sonst käme die Warnung nach jedem
    Kerzenschluss für bis zu einer Minute, bis der Bot die neue Kerze ausgewertet hat.
    """
    ende = letzte_startzeit + intervall
    return jetzt - ende > 2 * intervall


def sind_demo_daten(trades: Sequence[Trade]) -> bool:
    """True, wenn die Trades aus dem Backtest stammen.

    Die simulierte Börse des Backtests vergibt die Order-IDs «sim-1», «sim-2» und so weiter,
    Bitget vergibt Zahlen.
    """
    return any(trade.order_id.startswith("sim-") for trade in trades)


@dataclass(frozen=True)
class Wertpunkt:
    """Wert des Kontos zur Zeit eines gespeicherten Kontostands, alles in USDT."""

    zeit: datetime
    frei: float  # frei verfügbare USDT, wie gespeichert
    position: float  # offene Menge × letzter Schlusskurs, 0.0 ohne offene Position
    gesamt: float  # frei + position


def gesamtwerte(
    *,
    kontostaende: Sequence[Kontostand],
    trades: Sequence[Trade],
    signale: Sequence[SignalEintrag],
    intervall: timedelta,
) -> list[Wertpunkt]:
    """Gesamtwert des Kontos zu jedem gespeicherten Kontostand, in derselben Reihenfolge.

    `Kontostand.betrag` ist nur das frei verfügbare Guthaben. Bei einem Kauf sinkt es, ohne dass
    Geld verloren geht: Das Geld steckt danach in BTC. Darum zählt die offene Position mit, zum
    letzten Schlusskurs bewertet: Gesamtwert = freie USDT + offene Menge × Schlusskurs.

    Zu einem Kontostand gehören der letzte Trade bis und mit seiner Zeit und die letzte Kerze, die
    bis dann fertig war. tick() speichert Trade und Kontostand mit derselben Zeit, darum zählt ein
    Trade mit gleicher Zeit schon mit. Eine Kerze, die zu dieser Zeit noch lief, zählt nicht, auch
    wenn sie inzwischen gespeichert ist. `signale` braucht die älteste Kerze zuerst, wie
    `hole_signale()` sie liefert.

    Nicht zu sehen sind BTC-Reste aus dem Abrunden (weniger als 0.000001 BTC): Die Datenbank kennt
    nur die Menge der offenen Position.
    """
    # bisect braucht aufsteigend sortierte Zeiten. hole_trades() liefert die Trades in der
    # Reihenfolge des Speicherns, ein von Hand nachgetragener Trade stünde also am Ende.
    trades = sorted(trades, key=lambda trade: trade.zeit)
    trade_zeiten = [trade.zeit for trade in trades]
    kerzen_enden = [signal.startzeit + intervall for signal in signale]
    punkte = []
    for kontostand in kontostaende:
        if kontostand.waehrung != QUOTEWAEHRUNG:
            continue
        # bisect_right zählt, wie viele Einträge bis und mit dieser Zeit liegen.
        anzahl_trades = bisect_right(trade_zeiten, kontostand.zeit)
        anzahl_kerzen = bisect_right(kerzen_enden, kontostand.zeit)
        letzter_trade = trades[anzahl_trades - 1] if anzahl_trades > 0 else None
        if letzter_trade is None or letzter_trade.seite != "kauf":
            position = 0.0
        else:
            # Ohne fertige Kerze zählt der Kaufpreis. Bei tick() kommt das nicht vor: Er
            # speichert den Kontostand erst nach der Kerze.
            if anzahl_kerzen > 0:
                kurs = signale[anzahl_kerzen - 1].schlusskurs
            else:
                kurs = letzter_trade.preis
            position = letzter_trade.menge * kurs
        punkte.append(
            Wertpunkt(
                zeit=kontostand.zeit,
                frei=kontostand.betrag,
                position=position,
                gesamt=kontostand.betrag + position,
            )
        )
    return punkte


def zeitraum_beginn(
    *, letzte_startzeit: datetime, intervall: timedelta, dauer: timedelta | None
) -> datetime | None:
    """Beginn des angezeigten Zeitraums: `dauer` vor dem Ende der letzten Kerze, None für alles.

    Gemessen ab der letzten Kerze und nicht ab jetzt: Sonst bliebe das Bild bei alten Daten (z.B.
    aus dem Backtest) leer.
    """
    if dauer is None:
        return None
    return letzte_startzeit + intervall - dauer


# --- Zeilen für Charts und Tabellen ---


def kerzen_zeilen(signale: Sequence[SignalEintrag], *, intervall: timedelta) -> list[dict]:
    """Eine Zeile pro Kerze für den Kurs-Chart.

    Der Körper der Kerze reicht von `von` bis `bis`, mit etwas Abstand zu den Nachbarkerzen. Docht
    und Bänder stehen in der Mitte (`mitte`). `zeit` ist der Text für den Tooltip.
    """
    rand = intervall / 10
    return [
        {
            "von": zeit_fuer_chart(signal.startzeit + rand),
            "mitte": zeit_fuer_chart(signal.startzeit + intervall / 2),
            "bis": zeit_fuer_chart(signal.startzeit + intervall - rand),
            "zeit": zeit_als_text(signal.startzeit),
            "eroeffnung": signal.eroeffnung,
            "hoch": signal.hoch,
            "tief": signal.tief,
            "schlusskurs": signal.schlusskurs,
            "oberes_band": signal.oberes_band,
            "mittelband": signal.mittelband,
            "unteres_band": signal.unteres_band,
            "signal": signal.signal,
            "steigend": signal.schlusskurs >= signal.eroeffnung,
        }
        for signal in signale
    ]


def trade_zeilen(trades: Sequence[Trade]) -> list[dict]:
    """Eine Zeile pro Trade für die Markierungen im Kurs-Chart."""
    return [
        {
            "zeit_chart": zeit_fuer_chart(trade.zeit),
            "zeit": zeit_als_text(trade.zeit),
            "seite": SEITEN[trade.seite],
            "menge": trade.menge,
            "preis": trade.preis,
            "betrag": trade.menge * trade.preis,
            "order_id": trade.order_id,
        }
        for trade in trades
    ]


def wert_zeilen(werte: Sequence[Wertpunkt]) -> list[dict]:
    """Eine Zeile pro Wertpunkt für den Chart des Gesamtwerts."""
    return [
        {
            "zeit_chart": zeit_fuer_chart(wert.zeit),
            "zeit": zeit_als_text(wert.zeit),
            "frei": wert.frei,
            "position": wert.position,
            "gesamt": wert.gesamt,
        }
        for wert in werte
    ]


# --- Charts ---

# Farben der Charts. Bänder, Kauf und Verkauf bleiben auch bei Farbsehschwäche unterscheidbar und
# heben sich vom hellen wie vom dunklen Hintergrund von Streamlit ab (beides geprüft). Kauf und
# Verkauf haben dazu eigene Formen (▲ und ▼), die Farbe allein trägt also keine Bedeutung. Fest
# statt je nach Modus: Streamlit meldet beim ersten Laden nicht zuverlässig, ob der Browser hell
# oder dunkel anzeigt.
FARBE_BAND = "#2a78d6"  # Bollinger-Bänder und Gesamtwert
FARBE_KAUF = "#d95926"
FARBE_VERKAUF = "#199e70"
FARBE_GRAU = "#898781"  # Kerzen, Fadenkreuz und Startwert

# Apostroph als Tausendertrennzeichen auch in den Achsen und Tooltips der Charts.
SCHWEIZER_ZAHLEN = {
    "number": {"decimal": ".", "thousands": "'", "grouping": [3], "currency": ["", ""]}
}


def _zeitachse(feld: str) -> alt.X:
    # UTC-Skala: zeigt die Schweizer Uhrzeit aus zeit_fuer_chart() unverändert an.
    return alt.X(
        f"{feld}:T",
        title=None,
        scale=alt.Scale(type="utc"),
        axis=alt.Axis(format="%d.%m. %H:%M", labelAngle=0),
    )


def _kursachse(feld: str) -> alt.Y:
    # zero=False: Die Achse beginnt nahe beim tiefsten Kurs, nicht bei 0.
    return alt.Y(f"{feld}:Q", title="Kurs in USDT", scale=alt.Scale(zero=False))


def kurs_chart(*, kerzen: list[dict], trades: list[dict]) -> alt.LayerChart:
    """Kerzen, Bollinger-Bänder und die Käufe und Verkäufe übereinander in einem Chart."""
    # Ohne eigene Daten: Die Ebenen der Kerzen bekommen sie unten gemeinsam. Sonst stünden alle
    # Kerzen einmal pro Ebene im Chart, bei «Alles» wären das mehrere MB für den Browser.
    basis = alt.Chart()
    mitte = _zeitachse("mitte")

    # Bänder: blasse Fläche zwischen unterem und oberem Band, darauf die drei Linien.
    flaeche = basis.mark_area(color=FARBE_BAND, opacity=0.1).encode(
        x=mitte, y=_kursachse("unteres_band"), y2="oberes_band:Q"
    )
    oberes = basis.mark_line(color=FARBE_BAND, strokeWidth=1).encode(
        x=mitte, y=_kursachse("oberes_band")
    )
    unteres = basis.mark_line(color=FARBE_BAND, strokeWidth=1).encode(
        x=mitte, y=_kursachse("unteres_band")
    )
    mittelband = basis.mark_line(color=FARBE_BAND, strokeWidth=2).encode(
        x=mitte, y=_kursachse("mittelband")
    )

    # Kerzen: Docht von Tief bis Hoch, Körper von Eröffnung bis Schluss. Hohl heisst gestiegen.
    docht = basis.mark_rule(color=FARBE_GRAU, strokeWidth=1).encode(
        x=mitte, y=_kursachse("tief"), y2="hoch:Q"
    )
    koerper = basis.mark_rect(stroke=FARBE_GRAU, strokeWidth=1).encode(
        x=_zeitachse("von"),
        x2="bis:T",
        y=_kursachse("eroeffnung"),
        y2="schlusskurs:Q",
        fill=alt.when(alt.datum.steigend)
        .then(alt.value("transparent"))
        .otherwise(alt.value(FARBE_GRAU)),
    )

    # Fadenkreuz: Eine senkrechte Linie folgt der Maus zur nächsten Kerze und zeigt ihre Werte.
    unter_maus = alt.selection_point(
        name="kerze_unter_maus",
        nearest=True,
        on="pointerover",
        fields=["mitte"],
        empty=False,
        clear="pointerout",
    )
    fadenkreuz = (
        basis.mark_rule(color=FARBE_GRAU, strokeWidth=1)
        .encode(
            x=mitte,
            opacity=alt.when(unter_maus).then(alt.value(0.8)).otherwise(alt.value(0)),
            tooltip=[
                alt.Tooltip("zeit:N", title="Kerze ab"),
                alt.Tooltip("eroeffnung:Q", title="Eröffnung", format=",.2f"),
                alt.Tooltip("hoch:Q", title="Hoch", format=",.2f"),
                alt.Tooltip("tief:Q", title="Tief", format=",.2f"),
                alt.Tooltip("schlusskurs:Q", title="Schluss", format=",.2f"),
                alt.Tooltip("oberes_band:Q", title="oberes Band", format=",.2f"),
                alt.Tooltip("mittelband:Q", title="Mittelband", format=",.2f"),
                alt.Tooltip("unteres_band:Q", title="unteres Band", format=",.2f"),
                alt.Tooltip("signal:N", title="Signal"),
            ],
        )
        .add_params(unter_maus)
    )

    # Käufe ▲ und Verkäufe ▼ zum ausgeführten Preis.
    seiten = list(SEITEN.values())
    legende = alt.Legend(title=None, orient="top", symbolSize=150)
    markierungen = (
        alt.Chart(alt.Data(values=trades))
        .mark_point(filled=True, size=150, opacity=1)
        .encode(
            x=_zeitachse("zeit_chart"),
            y=_kursachse("preis"),
            color=alt.Color(
                "seite:N",
                scale=alt.Scale(domain=seiten, range=[FARBE_KAUF, FARBE_VERKAUF]),
                legend=legende,
            ),
            shape=alt.Shape(
                "seite:N",
                scale=alt.Scale(domain=seiten, range=["triangle-up", "triangle-down"]),
                legend=legende,
            ),
            tooltip=[
                alt.Tooltip("seite:N", title="Seite"),
                alt.Tooltip("zeit:N", title="Zeit"),
                alt.Tooltip("menge:Q", title="Menge in BTC", format=",.6f"),
                alt.Tooltip("preis:Q", title="Preis in USDT", format=",.2f"),
                alt.Tooltip("betrag:Q", title="Betrag in USDT", format=",.2f"),
                alt.Tooltip("order_id:N", title="Order-ID"),
            ],
        )
    )
    kerzen_ebenen = alt.layer(
        flaeche,
        oberes,
        unteres,
        mittelband,
        docht,
        koerper,
        fadenkreuz,
        data=alt.Data(values=kerzen),
    )
    return (
        alt.layer(kerzen_ebenen, markierungen)
        .properties(height=420)
        .configure(locale=SCHWEIZER_ZAHLEN)
    )


def gesamtwert_chart(*, werte: list[dict], startwert: float | None) -> alt.LayerChart:
    """Verlauf des Gesamtwerts als Linie, dazu eine Hilfslinie beim Startwert.

    `startwert=None` lässt die Hilfslinie weg. Das Dashboard tut das, wenn der gewählte Zeitraum
    erst nach dem Start beginnt: Sonst müsste die Achse bis zum Startwert reichen, und die Kurve
    würde flach.
    """
    basis = alt.Chart()  # Daten wie im Kurs-Chart einmal für alle Ebenen, siehe unten
    zeit = _zeitachse("zeit_chart")
    wert = alt.Y("gesamt:Q", title="Gesamtwert in USDT", scale=alt.Scale(zero=False))

    ebenen = []
    if startwert is not None:
        start = alt.Chart(alt.Data(values=[{"wert": startwert}]))
        startachse = alt.Y("wert:Q", scale=alt.Scale(zero=False))
        startlinie = start.mark_rule(color=FARBE_GRAU, strokeWidth=1).encode(y=startachse)
        starttext = start.mark_text(
            align="left", baseline="bottom", dx=4, dy=-4, color=FARBE_GRAU
        ).encode(x=alt.value(0), y=startachse, text=alt.value(f"Start {als_zahl(startwert)}"))
        ebenen = [startlinie, starttext]

    linie = basis.mark_line(color=FARBE_BAND, strokeWidth=2).encode(x=zeit, y=wert)
    unter_maus = alt.selection_point(
        name="wert_unter_maus",
        nearest=True,
        on="pointerover",
        fields=["zeit_chart"],
        empty=False,
        clear="pointerout",
    )
    fadenkreuz = (
        basis.mark_rule(color=FARBE_GRAU, strokeWidth=1)
        .encode(
            x=zeit,
            opacity=alt.when(unter_maus).then(alt.value(0.8)).otherwise(alt.value(0)),
            tooltip=[
                alt.Tooltip("zeit:N", title="Zeit"),
                alt.Tooltip("gesamt:Q", title="Gesamtwert", format=",.2f"),
                alt.Tooltip("frei:Q", title="freie USDT", format=",.2f"),
                alt.Tooltip("position:Q", title="offene Position", format=",.2f"),
            ],
        )
        .add_params(unter_maus)
    )
    punkt = (
        basis.mark_point(filled=True, size=80, opacity=1, color=FARBE_BAND)
        .encode(x=zeit, y=wert)
        .transform_filter(unter_maus)
    )
    return (
        alt.layer(*ebenen, alt.layer(linie, fadenkreuz, punkt, data=alt.Data(values=werte)))
        .properties(height=260)
        .configure(locale=SCHWEIZER_ZAHLEN)
    )


# --- Die Seite ---


def main() -> None:
    """Die Seite. Streamlit führt sie bei jedem Aufruf und jedem Klick neu aus."""
    st.set_page_config(page_title="Bollinger-Bot", page_icon="📈", layout="wide")
    st.title("Bollinger-Bot")
    try:
        konfiguration = lies_konfiguration(os.environ)
    except ValueError as fehler:
        st.error(str(fehler))
        return
    pfad = konfiguration.db_pfad
    intervall = konfiguration.intervall_dauer
    st.caption(
        f"BTC/USDT · {konfiguration.intervall}-Kerzen · Datenbank {pfad} · "
        "alle Zeiten in Schweizer Zeit"
    )

    # Speicher(pfad) würde eine fehlende Datei neu anlegen, das Dashboard schreibt aber nie.
    if not pfad.is_file():
        st.error(
            f"Keine Datenbank unter {pfad.absolute()} gefunden. BOT_DB_PFAD prüfen: Bot und "
            "Dashboard brauchen dieselbe Datei. Der Bot legt sie beim ersten Start an."
        )
        return
    with Speicher(pfad) as speicher:
        signale = speicher.hole_signale()
        trades = speicher.hole_trades()
        kontostaende = speicher.hole_kontostaende()
        letzte_startzeit = speicher.letzte_signal_startzeit()
        position = speicher.offene_position()
    if letzte_startzeit is None:
        st.info(
            "Noch keine ausgewerteten Kerzen. Der Bot speichert die erste, sobald eine Kerze "
            "abgeschlossen ist."
        )
        return

    if sind_demo_daten(trades):
        st.info(
            "**Demo-Daten:** Diese Trades stammen aus dem Backtest (Order-IDs «sim-…»), nicht "
            "vom Bitget-Konto.",
            icon="🧪",
        )
    if ist_veraltet(
        letzte_startzeit=letzte_startzeit, jetzt=datetime.now(UTC), intervall=intervall
    ):
        st.warning(
            "**Keine neuen Kerzen:** Die letzte ausgewertete Kerze endete am "
            f"{zeit_als_text(letzte_startzeit + intervall)}, vor mehr als zwei Intervallen "
            f"({konfiguration.intervall}). Läuft der Bot? Im Log nach Fehlern suchen, z.B. "
            "einem ungültigen API-Key.",
            icon="⚠️",
        )

    # Kennzahlen: immer der aktuelle Stand, unabhängig vom gewählten Zeitraum.
    werte = gesamtwerte(
        kontostaende=kontostaende, trades=trades, signale=signale, intervall=intervall
    )
    start = werte[0] if werte else None
    letzte_kerze = signale[-1]
    gesamt, frei, offen, kurs = st.columns(4)
    if start is not None:
        aktuell = werte[-1]
        differenz = aktuell.gesamt - start.gesamt
        prozent = f" ({differenz / start.gesamt * 100:+.2f} %)" if start.gesamt > 0 else ""
        gesamt.metric(
            "Gesamtwert (USDT)",
            als_zahl(aktuell.gesamt),
            delta=f"{als_zahl(differenz, vorzeichen=True)}{prozent} seit Start",
            help=f"Freie USDT plus offene Position. Start am {zeit_als_text(start.zeit)} mit "
            f"{als_zahl(start.gesamt)} USDT.",
            border=True,
        )
        frei.metric("Freie USDT", als_zahl(aktuell.frei), border=True)
    else:
        gesamt.metric("Gesamtwert (USDT)", "–", border=True)
        frei.metric("Freie USDT", "–", border=True)
    if position is not None:
        gewinn = (letzte_kerze.schlusskurs - position.preis) * position.menge
        offen.metric(
            "Offene Position (BTC)",
            als_zahl(position.menge, stellen=6),
            delta=f"{als_zahl(gewinn, vorzeichen=True)} USDT seit Kauf",
            help=f"Gekauft am {zeit_als_text(position.zeit)} zu {als_zahl(position.preis)} USDT.",
            border=True,
        )
    else:
        offen.metric("Offene Position (BTC)", "keine", border=True)
    kurs.metric(
        "Letzter Schlusskurs (USDT)",
        als_zahl(letzte_kerze.schlusskurs),
        help=f"Kerze ab {zeit_als_text(letzte_kerze.startzeit)}, Signal «{letzte_kerze.signal}».",
        border=True,
    )

    # Der Zeitraum gilt für alles darunter: beide Charts und die Tabellen.
    auswahl = st.radio("Zeitraum", list(ZEITRAEUME), index=1, horizontal=True)
    beginn = zeitraum_beginn(
        letzte_startzeit=letzte_startzeit, intervall=intervall, dauer=ZEITRAEUME[auswahl]
    )
    if beginn is not None:
        signale = [signal for signal in signale if signal.startzeit >= beginn]
        trades = [trade for trade in trades if trade.zeit >= beginn]
        werte = [wert for wert in werte if wert.zeit >= beginn]

    st.subheader("Kurs und Bollinger-Bänder")
    st.altair_chart(
        kurs_chart(
            kerzen=kerzen_zeilen(signale, intervall=intervall),
            trades=trade_zeilen(trades),
        ),
        width="stretch",
    )
    st.caption(
        "Kerze: Der Docht reicht vom Tief bis zum Hoch, der Körper von der Eröffnung bis zum "
        "Schluss, hohl heisst gestiegen, gefüllt gefallen. Blau sind die Bollinger-Bänder: "
        "dick das Mittelband (Durchschnitt der letzten Schlusskurse), dünn das obere und das "
        "untere Band. Der Bot kauft, wenn eine Kerze unter dem unteren Band schliesst (▲), und "
        "verkauft über dem Mittelband oder beim Stop-Loss (▼)."
    )

    if werte:
        # Die Hilfslinie beim Startwert nur, wenn der Start im gewählten Zeitraum liegt.
        startwert = start.gesamt if werte[0] == start else None
        st.subheader("Gesamtwert")
        st.altair_chart(
            gesamtwert_chart(werte=wert_zeilen(werte), startwert=startwert), width="stretch"
        )
        st.caption(
            "Freie USDT plus die offene Position zum letzten Schlusskurs. Die freien USDT allein "
            "sinken bei jedem Kauf, obwohl dabei kein Geld verloren geht."
        )

    st.subheader("Trades")
    if trades:
        st.dataframe(
            [
                {
                    "Zeit": zeit_als_text(trade.zeit),
                    "Seite": SEITEN[trade.seite],
                    "Menge (BTC)": trade.menge,
                    "Preis (USDT)": trade.preis,
                    "Betrag (USDT)": trade.menge * trade.preis,
                    "Order-ID": trade.order_id,
                }
                for trade in reversed(trades)  # neueste zuerst
            ],
            hide_index=True,
            column_config={
                "Menge (BTC)": st.column_config.NumberColumn(format="%.6f"),
                "Preis (USDT)": st.column_config.NumberColumn(format="%.2f"),
                "Betrag (USDT)": st.column_config.NumberColumn(format="%.2f"),
            },
        )
    else:
        st.caption("Keine Trades im gewählten Zeitraum.")

    with st.expander("Kerzen als Tabelle"):
        st.dataframe(
            [
                {
                    "Kerze ab": zeit_als_text(signal.startzeit),
                    "Eröffnung": signal.eroeffnung,
                    "Hoch": signal.hoch,
                    "Tief": signal.tief,
                    "Schluss": signal.schlusskurs,
                    "unteres Band": signal.unteres_band,
                    "Mittelband": signal.mittelband,
                    "oberes Band": signal.oberes_band,
                    "Signal": signal.signal,
                }
                for signal in reversed(signale)  # neueste zuerst
            ],
            hide_index=True,
        )


# `streamlit run` führt diese Datei als __main__ aus. Die Tests importieren sie nur, dann
# entsteht keine Seite.
if __name__ == "__main__":
    main()
