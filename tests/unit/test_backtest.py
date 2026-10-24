"""Unit-Tests für den Backtest: Kerzen aus der CSV lesen und die simulierte Börse.

Den ganzen Backtest mit echter SQLite-Datei testen die Integrationstests in
`tests/integration/test_backtest.py`.
"""

from datetime import UTC, datetime

from bollinger_bot.backtest import lies_kerzen
from bollinger_bot.port import Kerze

CSV_KOPF = "startzeit,eroeffnung,hoch,tief,schluss,volumen\n"


def test_lies_kerzen_liest_zeit_mit_utc_und_zahlen_als_float(tmp_path):
    # Zwei Zeilen im Format von scripts/kerzen_holen.py. Bitget lässt Nullen am Ende weg
    # («84927» statt «84927.00»), auch das muss als float ankommen.
    pfad = tmp_path / "kerzen.csv"
    pfad.write_text(
        CSV_KOPF
        + "2026-10-03T18:45:00+00:00,84940.34,84950.41,84927,84940.01,1.402479\n"
        + "2026-10-03T19:00:00+00:00,84940.01,84940.01,84848,84868,14.990596\n"
    )

    kerzen = lies_kerzen(pfad)

    assert kerzen == [
        Kerze(
            startzeit=datetime(2026, 10, 3, 18, 45, tzinfo=UTC),
            eroeffnung=84940.34,
            hoch=84950.41,
            tief=84927.0,
            schluss=84940.01,
            volumen=1.402479,
        ),
        Kerze(
            startzeit=datetime(2026, 10, 3, 19, 0, tzinfo=UTC),
            eroeffnung=84940.01,
            hoch=84940.01,
            tief=84848.0,
            schluss=84868.0,
            volumen=14.990596,
        ),
    ]
    assert all(isinstance(k.tief, float) for k in kerzen)
    assert kerzen[0].startzeit.utcoffset().total_seconds() == 0
