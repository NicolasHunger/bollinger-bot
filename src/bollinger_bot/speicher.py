"""SQLite-Speicher: hält Signale, Trades und Kontostände fest.

`tick()` schreibt hinein, das Dashboard liest daraus. Die Datei überlebt einen Neustart des Bots:
Danach weiss er aus den Trades, ob eine Position offen ist und zu welchem Preis gekauft wurde.

SQLite kennt keinen eigenen Datentyp für Zeitpunkte. Wir speichern sie als Text im Format
ISO 8601 in UTC, z.B. `2026-10-17T12:00:00+00:00`. In diesem Format sortiert SQLite die Texte
automatisch richtig nach Zeit.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signale (
    startzeit    TEXT PRIMARY KEY,  -- Beginn der Kerze; pro Kerze höchstens ein Eintrag
    schlusskurs  REAL NOT NULL,
    mittelband   REAL NOT NULL,
    oberes_band  REAL NOT NULL,
    unteres_band REAL NOT NULL,
    signal       TEXT NOT NULL CHECK (signal IN ('kaufen', 'verkaufen', 'halten'))
);

CREATE TABLE IF NOT EXISTS trades (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    zeit     TEXT NOT NULL,
    seite    TEXT NOT NULL CHECK (seite IN ('kauf', 'verkauf')),
    menge    REAL NOT NULL,
    preis    REAL NOT NULL,
    order_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS kontostaende (
    zeit     TEXT NOT NULL,
    waehrung TEXT NOT NULL,
    betrag   REAL NOT NULL
);
"""


@dataclass(frozen=True)
class SignalEintrag:
    """Auswertung einer abgeschlossenen Kerze: Kurs, Bänder und Entscheid."""

    startzeit: datetime
    schlusskurs: float
    mittelband: float
    oberes_band: float
    unteres_band: float
    signal: str  # "kaufen", "verkaufen" oder "halten"


@dataclass(frozen=True)
class Trade:
    """Ein ausgeführter Kauf oder Verkauf."""

    zeit: datetime
    seite: str  # "kauf" oder "verkauf"
    menge: float
    preis: float
    order_id: str


@dataclass(frozen=True)
class Kontostand:
    zeit: datetime
    waehrung: str
    betrag: float


def _als_text(zeit: datetime) -> str:
    if zeit.tzinfo is None:
        raise ValueError(
            f"Zeit ohne Zeitzone: {zeit}. Bitte mit Zeitzone angeben, z.B. tzinfo=UTC."
        )
    # Immer in UTC und auf Sekunden genau: gleich lange Texte sortieren sicher richtig.
    return zeit.astimezone(UTC).isoformat(timespec="seconds")


def _als_zeit(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Speicher:
    """Zugriff auf die SQLite-Datei. Legt die Tabellen beim ersten Öffnen selbst an."""

    def __init__(self, pfad: str | Path):
        self._db = sqlite3.connect(pfad)
        # WAL-Modus: Das Dashboard kann lesen, während der Bot schreibt, ohne dass einer wartet.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def schliessen(self) -> None:
        self._db.close()

    # Erlaubt `with Speicher(pfad) as speicher:`, die Datei wird am Ende automatisch geschlossen.
    def __enter__(self) -> "Speicher":
        return self

    def __exit__(self, *_) -> None:
        self.schliessen()

    # --- Signale ---

    def speichere_signal(self, eintrag: SignalEintrag) -> bool:
        """Speichert die Auswertung einer Kerze.

        Gibt False zurück, wenn diese Kerze schon gespeichert ist. Das passiert, wenn die
        Polling-Schleife dieselbe Kerze zweimal sieht, und ist kein Fehler.
        """
        # `ON CONFLICT ... DO NOTHING` ignoriert nur eine doppelte Startzeit. Ein ungültiges
        # Signal verletzt weiterhin den CHECK und wirft einen Fehler. (`INSERT OR IGNORE` würde
        # auch den ungültigen Eintrag stillschweigend verwerfen.)
        with self._db:
            cursor = self._db.execute(
                """INSERT INTO signale
                   (startzeit, schlusskurs, mittelband, oberes_band, unteres_band, signal)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (startzeit) DO NOTHING""",
                (
                    _als_text(eintrag.startzeit),
                    eintrag.schlusskurs,
                    eintrag.mittelband,
                    eintrag.oberes_band,
                    eintrag.unteres_band,
                    eintrag.signal,
                ),
            )
        return cursor.rowcount == 1

    def hole_signale(self) -> list[SignalEintrag]:
        """Alle gespeicherten Signale, älteste zuerst."""
        zeilen = self._db.execute(
            """SELECT startzeit, schlusskurs, mittelband, oberes_band, unteres_band, signal
               FROM signale ORDER BY startzeit"""
        ).fetchall()
        return [SignalEintrag(_als_zeit(zeile[0]), *zeile[1:]) for zeile in zeilen]

    def letzte_signal_startzeit(self) -> datetime | None:
        """Startzeit der zuletzt ausgewerteten Kerze, None bei leerer Datenbank."""
        (text,) = self._db.execute("SELECT MAX(startzeit) FROM signale").fetchone()
        return _als_zeit(text) if text is not None else None

    # --- Trades ---

    def speichere_trade(self, trade: Trade) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO trades (zeit, seite, menge, preis, order_id) VALUES (?, ?, ?, ?, ?)",
                (_als_text(trade.zeit), trade.seite, trade.menge, trade.preis, trade.order_id),
            )

    def hole_trades(self) -> list[Trade]:
        """Alle Trades in der Reihenfolge, in der sie gespeichert wurden."""
        zeilen = self._db.execute(
            "SELECT zeit, seite, menge, preis, order_id FROM trades ORDER BY id"
        ).fetchall()
        return [Trade(_als_zeit(zeile[0]), *zeile[1:]) for zeile in zeilen]

    def offene_position(self) -> Trade | None:
        """Der letzte Kauf, falls danach noch nicht verkauft wurde, sonst None."""
        zeile = self._db.execute(
            "SELECT zeit, seite, menge, preis, order_id FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if zeile is None or zeile[1] != "kauf":
            return None
        return Trade(_als_zeit(zeile[0]), *zeile[1:])

    # --- Kontostände ---

    def speichere_kontostand(self, kontostand: Kontostand) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO kontostaende (zeit, waehrung, betrag) VALUES (?, ?, ?)",
                (_als_text(kontostand.zeit), kontostand.waehrung, kontostand.betrag),
            )

    def hole_kontostaende(self) -> list[Kontostand]:
        """Alle Kontostände, älteste zuerst."""
        zeilen = self._db.execute(
            "SELECT zeit, waehrung, betrag FROM kontostaende ORDER BY zeit"
        ).fetchall()
        return [Kontostand(_als_zeit(zeile[0]), *zeile[1:]) for zeile in zeilen]
