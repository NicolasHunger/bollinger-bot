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

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bollinger_bot.speicher import Kontostand, SignalEintrag, Trade

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
