"""Einstiegspunkt: liest die Konfiguration aus der Umgebung und startet den Bot."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bollinger_bot.bitget_client import INTERVALLE
from bollinger_bot.tick import Einstellungen


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
    intervall = umgebung.get("BOT_INTERVALL", "15min")
    return Konfiguration(
        api_key=umgebung["BITGET_API_KEY"],
        api_secret=umgebung["BITGET_API_SECRET"],
        passphrase=umgebung["BITGET_PASSPHRASE"],
        db_pfad=Path(umgebung.get("BOT_DB_PFAD", "data/bot.db")),
        einstellungen=Einstellungen(intervall=intervall, intervall_dauer=INTERVALLE[intervall]),
    )
