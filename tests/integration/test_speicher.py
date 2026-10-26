"""Integrationstests für den SQLite-Speicher.

Die Tests laufen gegen eine echte SQLite-Datei. `tmp_path` ist eine pytest-Fixture: ein leerer
Ordner, den pytest für jeden Test neu anlegt und danach wegräumt.
"""

import math
import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from bollinger_bot.speicher import Kontostand, SignalEintrag, Speicher, Trade

ZEIT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def pfad(tmp_path):
    return tmp_path / "bot.db"


@pytest.fixture
def speicher(pfad):
    with Speicher(pfad) as speicher:
        yield speicher


def signal(
    startzeit=ZEIT,
    signal="halten",
    *,
    eroeffnung=99.0,
    hoch=102.0,
    tief=97.0,
    schlusskurs=100.0,
    mittelband=100.0,
    oberes_band=110.0,
    unteres_band=90.0,
):
    return SignalEintrag(
        startzeit=startzeit,
        eroeffnung=eroeffnung,
        hoch=hoch,
        tief=tief,
        schlusskurs=schlusskurs,
        mittelband=mittelband,
        oberes_band=oberes_band,
        unteres_band=unteres_band,
        signal=signal,
    )


def trade(seite, order_id, *, menge=0.001, preis=100.0, zeit=ZEIT):
    return Trade(zeit=zeit, seite=seite, menge=menge, preis=preis, order_id=order_id)


# --- Neue Datenbank ---


def test_neue_datenbank_ist_leer(speicher):
    assert speicher.hole_signale() == []
    assert speicher.hole_trades() == []
    assert speicher.hole_kontostaende() == []
    assert speicher.letzte_signal_startzeit() is None
    assert speicher.offene_position() is None


def test_fehlender_ordner_wird_angelegt(tmp_path):
    # Wie BOT_DB_PFAD=data/bot.db auf einer frischen VM, auf der es data/ noch nicht gibt.
    pfad = tmp_path / "data" / "bot.db"

    with Speicher(pfad) as speicher:
        speicher.speichere_signal(signal())

    assert pfad.is_file()


def test_daten_ueberleben_einen_neustart(pfad):
    # Der Bot läuft im Container: Nach einem Neustart muss er seine offene Position noch kennen.
    with Speicher(pfad) as speicher:
        speicher.speichere_signal(signal())
        speicher.speichere_trade(trade("kauf", "A1"))

    with Speicher(pfad) as speicher:
        assert speicher.hole_signale() == [signal()]
        assert speicher.offene_position() == trade("kauf", "A1")


# --- Signale ---


def test_signal_wird_unveraendert_gespeichert_und_gelesen(speicher):
    # Lauter verschiedene Werte: Vertauscht der Speicher zwei Spalten, fällt es hier auf.
    eintrag = signal(
        signal="kaufen",
        eroeffnung=92.0,
        hoch=93.0,
        tief=89.0,
        schlusskurs=89.5,
        mittelband=100.0,
        oberes_band=110.0,
        unteres_band=90.0,
    )

    assert speicher.speichere_signal(eintrag) is True
    assert speicher.hole_signale() == [eintrag]


def test_dieselbe_kerze_wird_nur_einmal_gespeichert(speicher):
    # Die Polling-Schleife kann dieselbe Kerze zweimal sehen: der zweite Eintrag wird ignoriert.
    assert speicher.speichere_signal(signal(signal="kaufen")) is True
    assert speicher.speichere_signal(signal(signal="halten")) is False

    assert [s.signal for s in speicher.hole_signale()] == ["kaufen"]


def test_signale_kommen_nach_zeit_sortiert_zurueck(speicher):
    speicher.speichere_signal(signal(startzeit=ZEIT + timedelta(minutes=30)))
    speicher.speichere_signal(signal(startzeit=ZEIT))
    speicher.speichere_signal(signal(startzeit=ZEIT + timedelta(minutes=15)))

    assert [s.startzeit for s in speicher.hole_signale()] == [
        ZEIT,
        ZEIT + timedelta(minutes=15),
        ZEIT + timedelta(minutes=30),
    ]
    assert speicher.letzte_signal_startzeit() == ZEIT + timedelta(minutes=30)


def test_ungueltiges_signal_wird_von_der_datenbank_abgelehnt(speicher):
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_signal(signal(signal="vielleicht"))

    assert speicher.hole_signale() == []


# --- Signale: Regeln für Kurse und Bänder ---


@pytest.mark.parametrize("feld", ["eroeffnung", "hoch", "tief", "schlusskurs"])
def test_kurs_null_wird_abgelehnt(speicher, feld):
    # Genau auf der Grenze: 0 ist kein Kurs. Ehrlich gesagt greift > 0 allein nur beim Tief:
    # Eröffnung, Hoch oder Schluss von 0 liegen auch unter dem Tief von 97 und verletzen schon
    # die Regel tief <= … <= hoch.
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_signal(signal(**{feld: 0.0}))

    assert speicher.hole_signale() == []


def test_kleinster_kurs_ueber_null_wird_gespeichert(speicher):
    # Knapp über der Grenze, als flache Kerze: Eröffnung = Hoch = Tief = Schluss = 0.000001.
    # Zeigt nebenbei, dass Eröffnung und Schluss genau auf Hoch oder Tief liegen dürfen.
    eintrag = signal(eroeffnung=0.000001, hoch=0.000001, tief=0.000001, schlusskurs=0.000001)

    assert speicher.speichere_signal(eintrag) is True
    assert speicher.hole_signale() == [eintrag]


@pytest.mark.parametrize(
    "kurse",
    [
        {"eroeffnung": 102.000001},  # knapp über dem Hoch von 102
        {"eroeffnung": 96.999999},  # knapp unter dem Tief von 97
        {"schlusskurs": 102.000001},
        {"schlusskurs": 96.999999},
        {"hoch": 97.0, "tief": 102.0},  # Hoch und Tief vertauscht
    ],
    ids=[
        "eroeffnung-ueber-hoch",
        "eroeffnung-unter-tief",
        "schluss-ueber-hoch",
        "schluss-unter-tief",
        "hoch-unter-tief",
    ],
)
def test_kurs_ausserhalb_der_kerze_wird_abgelehnt(speicher, kurse):
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_signal(signal(**kurse))

    assert speicher.hole_signale() == []


def test_kurs_nan_wird_abgelehnt(speicher):
    # SQLite speichert NaN als NULL. Darum meldet nicht der CHECK, sondern NOT NULL den Fehler.
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
        speicher.speichere_signal(signal(schlusskurs=math.nan))

    assert speicher.hole_signale() == []


def test_gleiche_baender_werden_gespeichert(speicher):
    # Genau auf der Grenze: Sind alle n Kurse gleich, ist σ = 0 und die drei Bänder fallen
    # zusammen.
    eintrag = signal(mittelband=100.0, oberes_band=100.0, unteres_band=100.0)

    assert speicher.speichere_signal(eintrag) is True
    assert speicher.hole_signale() == [eintrag]


def test_negatives_unteres_band_wird_gespeichert(speicher):
    # Bänder dürfen negativ sein. Kurse 1, 2, …, 20 mit n = 20, k = 2:
    # Mittelband = 210 / 20 = 10.5, σ² = (20² − 1) / 12 = 33.25, σ ≈ 5.766
    # unteres Band = 10.5 − 2 · 5.766 ≈ −1.03, oberes Band = 10.5 + 2 · 5.766 ≈ 22.03
    eintrag = signal(
        eroeffnung=19.0,
        hoch=20.5,
        tief=18.5,
        schlusskurs=20.0,
        mittelband=10.5,
        oberes_band=22.03,
        unteres_band=-1.03,
    )

    assert speicher.speichere_signal(eintrag) is True
    assert speicher.hole_signale() == [eintrag]


@pytest.mark.parametrize(
    "baender",
    [
        {"unteres_band": 100.000001},  # unteres knapp über dem Mittelband von 100
        {"oberes_band": 99.999999},  # oberes knapp unter dem Mittelband von 100
        {"oberes_band": 90.0, "unteres_band": 110.0},  # oben und unten vertauscht
    ],
    ids=["unteres-ueber-mittel", "oberes-unter-mittel", "vertauscht"],
)
def test_baender_in_falscher_reihenfolge_werden_abgelehnt(speicher, baender):
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_signal(signal(**baender))

    assert speicher.hole_signale() == []


# --- Zeiten ---


def test_zeit_in_anderer_zeitzone_wird_als_utc_gespeichert(speicher):
    # 14:00 Schweizer Sommerzeit (UTC+2) ist 12:00 UTC
    sommerzeit = timezone(timedelta(hours=2))
    speicher.speichere_signal(signal(startzeit=datetime(2026, 10, 17, 14, 0, tzinfo=sommerzeit)))

    gelesen = speicher.letzte_signal_startzeit()
    assert gelesen == ZEIT
    assert gelesen.utcoffset() == timedelta(0)


OHNE_ZONE = datetime(2026, 10, 17, 12, 0)


@pytest.mark.parametrize(
    "speichern",
    [
        lambda speicher: speicher.speichere_signal(signal(startzeit=OHNE_ZONE)),
        lambda speicher: speicher.speichere_trade(trade("kauf", "A1", zeit=OHNE_ZONE)),
        lambda speicher: speicher.speichere_kontostand(
            Kontostand(zeit=OHNE_ZONE, waehrung="USDT", betrag=1000.0)
        ),
    ],
    ids=["signal", "trade", "kontostand"],
)
def test_zeit_ohne_zeitzone_wird_abgelehnt(speicher, speichern):
    # Ohne Zeitzone ist unklar, ob Schweizer Zeit oder UTC gemeint ist.
    with pytest.raises(ValueError, match="Zeit ohne Zeitzone"):
        speichern(speicher)

    assert speicher.hole_signale() == []
    assert speicher.hole_trades() == []
    assert speicher.hole_kontostaende() == []


def test_sekundenbruchteile_bleiben_erhalten(speicher):
    # datetime.now(UTC) hat Mikrosekunden. Sie dürfen beim Speichern nicht verloren gehen.
    kauf = trade("kauf", "A1", zeit=datetime(2026, 10, 17, 12, 0, 0, 123456, tzinfo=UTC))
    speicher.speichere_trade(kauf)

    assert speicher.hole_trades() == [kauf]


# --- Trades und offene Position ---


def test_nach_einem_kauf_ist_die_position_offen(speicher):
    speicher.speichere_trade(trade("kauf", "A1", preis=60_000))

    position = speicher.offene_position()
    assert position is not None
    assert position.preis == 60_000


def test_nach_dem_verkauf_ist_keine_position_offen(speicher):
    speicher.speichere_trade(trade("kauf", "A1"))
    speicher.speichere_trade(trade("verkauf", "A2"))

    assert speicher.offene_position() is None


def test_offene_position_ist_immer_der_letzte_kauf(speicher):
    speicher.speichere_trade(trade("kauf", "A1", preis=60_000))
    speicher.speichere_trade(trade("verkauf", "A2", preis=61_000))
    speicher.speichere_trade(trade("kauf", "A3", preis=59_000))

    assert speicher.offene_position() == trade("kauf", "A3", preis=59_000)
    assert [t.order_id for t in speicher.hole_trades()] == ["A1", "A2", "A3"]


def test_kauf_bei_offener_position_wird_abgelehnt(speicher):
    # Höchstens eine Position: Sonst würde die Menge des ersten Kaufs nie verkauft.
    speicher.speichere_trade(trade("kauf", "A1"))

    with pytest.raises(ValueError, match="schon eine Position offen"):
        speicher.speichere_trade(trade("kauf", "A2"))

    assert speicher.hole_trades() == [trade("kauf", "A1")]


@pytest.mark.parametrize(
    "vorher",
    [[], [trade("kauf", "A1"), trade("verkauf", "A2")]],
    ids=["leere-datenbank", "schon-verkauft"],
)
def test_verkauf_ohne_offene_position_wird_abgelehnt(speicher, vorher):
    for t in vorher:
        speicher.speichere_trade(t)

    with pytest.raises(ValueError, match="keine Position offen"):
        speicher.speichere_trade(trade("verkauf", "A3"))

    assert speicher.hole_trades() == vorher


def test_ungueltige_seite_wird_abgelehnt(speicher):
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_trade(trade("leerverkauf", "A1"))

    assert speicher.hole_trades() == []


def test_dieselbe_order_wird_nicht_doppelt_gespeichert(speicher):
    # Eine doppelte Order-ID deutet auf einen Fehler in tick() hin und soll auffallen.
    speicher.speichere_trade(trade("kauf", "A1"))

    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_trade(trade("verkauf", "A1"))

    assert speicher.offene_position() == trade("kauf", "A1")


@pytest.mark.parametrize("feld", ["menge", "preis"])
def test_menge_oder_preis_null_wird_abgelehnt(speicher, feld):
    # Genau auf der Grenze: 0 wird abgelehnt.
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_trade(trade("kauf", "A1", **{feld: 0.0}))

    assert speicher.hole_trades() == []


@pytest.mark.parametrize("feld", ["menge", "preis"])
def test_kleinste_menge_oder_preis_ueber_null_wird_gespeichert(speicher, feld):
    # Knapp über der Grenze: 0.000001 wird gespeichert.
    kauf = trade("kauf", "A1", **{feld: 0.000001})
    speicher.speichere_trade(kauf)

    assert speicher.hole_trades() == [kauf]


# --- Kontostände ---


def test_kontostaende_kommen_nach_zeit_sortiert_zurueck(speicher):
    spaeter = Kontostand(zeit=ZEIT + timedelta(hours=1), waehrung="USDT", betrag=990.0)
    frueher = Kontostand(zeit=ZEIT, waehrung="USDT", betrag=1000.0)
    speicher.speichere_kontostand(spaeter)
    speicher.speichere_kontostand(frueher)

    assert speicher.hole_kontostaende() == [frueher, spaeter]


def test_kontostand_null_wird_gespeichert(speicher):
    # Genau auf der Grenze: Ein leeres Konto ist erlaubt.
    leer = Kontostand(zeit=ZEIT, waehrung="USDT", betrag=0.0)
    speicher.speichere_kontostand(leer)

    assert speicher.hole_kontostaende() == [leer]


def test_negativer_kontostand_wird_abgelehnt(speicher):
    # Knapp unter der Grenze.
    with pytest.raises(sqlite3.IntegrityError):
        speicher.speichere_kontostand(
            Kontostand(zeit=ZEIT, waehrung="USDT", betrag=-0.000001)
        )

    assert speicher.hole_kontostaende() == []


# --- Bot schreibt, Dashboard liest ---


def test_zweiter_speicher_liest_den_bestaetigten_stand_waehrend_geschrieben_wird(pfad):
    with Speicher(pfad) as bot:
        bot.speichere_signal(signal())

    # Ein Schreiber beginnt eine Transaktion und fügt eine zweite Kerze ein, ohne zu bestätigen.
    # isolation_level=None: sqlite3 beginnt keine eigene Transaktion, wir steuern sie selbst.
    schreiber = sqlite3.connect(pfad, isolation_level=None)
    try:
        schreiber.execute("BEGIN IMMEDIATE")
        schreiber.execute(
            "INSERT INTO signale VALUES "
            "('2026-10-17T12:15:00.000000+00:00', 99, 102, 97, 100, 100, 110, 90, 'halten')"
        )

        # Das Dashboard öffnet die Datei und liest sofort, ohne auf den Schreiber zu warten.
        # Die unbestätigte Kerze sieht es nicht, nach dem COMMIT schon.
        with Speicher(pfad) as dashboard:
            assert dashboard.hole_signale() == [signal()]
            schreiber.execute("COMMIT")
            assert len(dashboard.hole_signale()) == 2
    finally:
        schreiber.close()

    # Ehrlich gesagt besteht SQLite den Teil oben auch ohne WAL: Solange der Schreiber nicht
    # bestätigt, darf man auch sonst lesen. Ohne WAL wartet aber der Schreiber beim COMMIT, bis
    # niemand mehr liest. Der eigentliche Nachweis für WAL ist darum dieser Assert. Der Modus
    # steht in der Datei, darum meldet ihn auch eine neue, rohe Verbindung.
    pruefer = sqlite3.connect(pfad)
    try:
        assert pruefer.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        pruefer.close()
