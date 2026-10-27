"""Polling: «Ist eine neue Kerze fertig?» und die Schleife, die `tick()` jede Minute aufruft.

Die Zeitrechnung ist reine Logik: Sie bekommt die aktuelle Zeit als Argument und fragt nie selbst
die Uhr. Die Schleife bekommt die Funktion zum Warten übergeben. So lässt sich beides mit einer
Fake-Uhr testen, ohne echt zu warten.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

EINE_MINUTE = timedelta(minutes=1)
EINE_STUNDE = timedelta(hours=1)


def letzte_abgeschlossene_kerze(jetzt: datetime, intervall: timedelta) -> datetime:
    """Startzeit der jüngsten Kerze, die zum Zeitpunkt `jetzt` abgeschlossen ist.

    Beispiel mit 15-Minuten-Kerzen: Um 12:16 läuft die Kerze ab 12:15 noch, abgeschlossen ist die
    Kerze ab 12:00. Kerzen beginnen bei vollen Vielfachen des Intervalls in UTC (12:00, 12:15, …).
    """
    if jetzt.tzinfo is None:
        raise ValueError(f"Zeit ohne Zeitzone: {jetzt}. Bitte mit Zeitzone angeben.")
    # Nur ganze Minuten, die eine Stunde ohne Rest teilen (1, 5, 15, 30 Minuten, 1 Stunde). Welche
    # Texte wie "15min" der Bot unterstützt, steht in der Tabelle INTERVALLE im Bitget-Client.
    # tick() übersetzt den Text damit in ein timedelta. Sekunden-Kerzen hat Bitget nicht. Längere
    # Kerzen brauchen wir nicht, und ob Bitget sie nach UTC ausrichtet, haben wir nicht geprüft.
    if (
        intervall <= timedelta(0)
        or intervall % EINE_MINUTE != timedelta(0)
        or EINE_STUNDE % intervall != timedelta(0)
    ):
        raise ValueError(
            "Intervall muss aus ganzen Minuten bestehen und eine Stunde ohne Rest teilen "
            f"(z.B. 1, 5, 15, 30 Minuten oder 1 Stunde), erhalten: {intervall}"
        )

    # Sekunden seit 1.1.1970 UTC auf ein Vielfaches des Intervalls abrunden: Das ergibt den
    # Start der laufenden Kerze. Die abgeschlossene Kerze davor beginnt ein Intervall früher.
    sekunden = int(jetzt.timestamp())
    intervall_sekunden = int(intervall.total_seconds())
    start_laufende_kerze = sekunden - sekunden % intervall_sekunden
    return datetime.fromtimestamp(start_laufende_kerze, UTC) - intervall


def neue_kerze_fertig(
    jetzt: datetime, intervall: timedelta, zuletzt_ausgewertet: datetime | None
) -> bool:
    """True, wenn seit der zuletzt ausgewerteten Kerze eine neue Kerze abgeschlossen ist.

    `zuletzt_ausgewertet` ist die Startzeit der letzten gespeicherten Kerze, None beim ersten Start.
    """
    if zuletzt_ausgewertet is None:
        return True
    return letzte_abgeschlossene_kerze(jetzt, intervall) > zuletzt_ausgewertet


def polling_schleife(
    tick: Callable[[], None],
    *,
    pause_sekunden: float = 60,
    schlafen: Callable[[float], None] = time.sleep,
    durchlaeufe: int | None = None,
) -> None:
    """Ruft `tick()` auf, wartet `pause_sekunden` und wiederholt das.

    `durchlaeufe=None` heisst: endlos, so läuft der Bot im Container. Die Tests geben eine feste
    Zahl an, damit die Schleife endet.
    """
    durchgang = 0
    while durchlaeufe is None or durchgang < durchlaeufe:
        try:
            tick()
        except Exception:
            # Ein Fehler (z.B. Bitget kurz nicht erreichbar) darf den Bot nicht beenden: Fehler
            # protokollieren und beim nächsten Durchgang neu versuchen. Nur `Exception` abfangen,
            # nie `BaseException`: Ctrl+C und `docker stop` (Docker Compose mit
            # `stop_signal: SIGINT`) kommen als `KeyboardInterrupt` an und müssen die Schleife
            # beenden.
            log.exception("tick() fehlgeschlagen, neuer Versuch in %s Sekunden", pause_sekunden)
        schlafen(pause_sekunden)
        durchgang += 1
