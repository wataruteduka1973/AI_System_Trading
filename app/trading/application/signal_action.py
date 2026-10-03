"""What every signal generator returns for one bar, whichever strategy produced it."""

from typing import Literal

SignalAction = Literal["buy", "sell", "hold"]
