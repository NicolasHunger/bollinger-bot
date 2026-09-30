"""Beweist, dass das Paket installiert ist und pytest läuft."""

import bollinger_bot


def test_paket_ist_importierbar():
    assert bollinger_bot.__name__ == "bollinger_bot"
