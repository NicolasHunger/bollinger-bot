"""Einstiegspunkt: liest die Konfiguration aus der Umgebung und startet den Bot."""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bollinger_bot.bitget_client import INTERVALLE, BitgetClient
from bollinger_bot.polling import polling_schleife
from bollinger_bot.speicher import Speicher
from bollinger_bot.tick import Einstellungen, tick

log = logging.getLogger(__name__)

PFLICHT = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE")


@dataclass(frozen=True)
class Konfiguration:
    """Alles, was der Bot aus den Umgebungsvariablen braucht.

    Die Keys stehen nicht in `repr()`, damit sie in keinem Log und keinem Traceback erscheinen.
    """

    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    passphrase: str = field(repr=False)
    db_pfad: Path
    einstellungen: Einstellungen


def lies_konfiguration(umgebung: Mapping[str, str]) -> Konfiguration:
    """Liest die Konfiguration aus den Umgebungsvariablen (`os.environ` oder ein Dictionary)."""
    fehlend = [name for name in PFLICHT if not umgebung.get(name)]
    if fehlend:
        raise ValueError(f"Umgebungsvariablen fehlen oder sind leer: {', '.join(fehlend)}")
    for name in PFLICHT:
        wert = umgebung[name]
        # isprintable() ist False für Steuerzeichen wie Tabulator oder «\r», nicht für Leerzeichen
        if wert != wert.strip() or not wert.isprintable():
            # Nur den Namen nennen, nie den Wert: Die Meldung landet im Log.
            raise ValueError(
                f"{name} hat Leerzeichen am Rand oder Steuerzeichen (z.B. einen Zeilenumbruch). "
                "Bitte den Wert in der .env prüfen."
            )
    intervall = umgebung.get("BOT_INTERVALL", "15min")
    if intervall not in INTERVALLE:
        erlaubt = ", ".join(INTERVALLE)
        raise ValueError(f"BOT_INTERVALL {intervall!r} ist ungültig, erlaubt: {erlaubt}")
    return Konfiguration(
        api_key=umgebung["BITGET_API_KEY"],
        api_secret=umgebung["BITGET_API_SECRET"],
        passphrase=umgebung["BITGET_PASSPHRASE"],
        db_pfad=Path(umgebung.get("BOT_DB_PFAD", "data/bot.db")),
        einstellungen=Einstellungen(intervall=intervall, intervall_dauer=INTERVALLE[intervall]),
    )


def _jetzt() -> datetime:
    return datetime.now(UTC)


def starte(
    konfiguration: Konfiguration,
    *,
    client_fabrik: Callable[..., BitgetClient] = BitgetClient,
    uhr: Callable[[], datetime] = _jetzt,
    schlafen: Callable[[float], None] = time.sleep,
    durchlaeufe: int | None = None,
) -> None:
    """Erstellt den Bitget-Client, öffnet die Datenbank und ruft tick() jede Minute auf.

    `durchlaeufe=None` heisst: endlos, bis Ctrl+C oder `docker stop`. Client, Uhr und Warten
    sind Argumente, damit die Tests einen Mock und eine Fake-Uhr übergeben können.
    """
    einstellungen = konfiguration.einstellungen
    # Nie die Keys loggen, nur womit der Bot handelt.
    log.info(
        "Bot startet: %s, %s-Kerzen, Datenbank %s",
        einstellungen.symbol,
        einstellungen.intervall,
        konfiguration.db_pfad,
    )
    # Die Keys gehen an den Konstruktor, nur hier sind sie bekannt (Abmachung).
    client = client_fabrik(
        api_key=konfiguration.api_key,
        api_secret=konfiguration.api_secret,
        passphrase=konfiguration.passphrase,
    )
    try:
        with Speicher(konfiguration.db_pfad) as speicher:
            polling_schleife(
                lambda: tick(
                    boerse=client,
                    speicher=speicher,
                    einstellungen=einstellungen,
                    jetzt=uhr(),
                ),
                schlafen=schlafen,
                durchlaeufe=durchlaeufe,
            )
    finally:
        # Auch bei Ctrl+C oder einem Fehler: offene Verbindungen zu Bitget schliessen.
        client.schliessen()


def main(umgebung: Mapping[str, str] | None = None, *, starte=starte) -> int:
    """Startet den Bot. Gibt den Exit-Code zurück: 2 bei falscher Konfiguration, sonst 0."""
    try:
        konfiguration = lies_konfiguration(umgebung)
    except ValueError as fehler:
        # Nur die Meldung, kein Traceback: Sie sagt schon, welche Variable falsch ist.
        log.error("%s", fehler)
        return 2
    starte(konfiguration)
    return 0
