"""Backtest: der echte `tick()` auf aufgezeichneten Kursen statt auf Bitget."""

from pathlib import Path

from bollinger_bot.port import Kerze


def lies_kerzen(pfad: str | Path) -> list[Kerze]:
    return []
