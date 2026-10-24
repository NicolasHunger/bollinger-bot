"""Einstiegspunkt: liest die Konfiguration aus der Umgebung und startet den Bot."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bollinger_bot.bitget_client import INTERVALLE
from bollinger_bot.tick import Einstellungen

PFLICHT = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE")


@dataclass(frozen=True)
class Konfiguration:
    """Alles, was der Bot aus den Umgebungsvariablen braucht."""

    api_key: str
    api_secret: str
    passphrase: str
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
