from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict


TICK_S = 1.0 / 20.0


VOLTAGE_BY_TIER: Dict[str, int] = {
    "ULV": 8,
    "LV": 32,
    "MV": 128,
    "HV": 512,
    "EV": 2048,
    "IV": 8192,
    "LuV": 32768,
    "ZPM": 131072,
    "UV": 524288,
    "UHV": 2_097_152,
    "UEV": 8_388_608,
}


@dataclass
class OCResult:
    overclocks: int
    ticks: int
    seconds: float
    eut: float


def compute_overclock(base_time_s: float, base_eut: float, tier: str) -> OCResult:
    """Nomifactory OC rules.

    n = floor(log4(machine_voltage/base_eut)) with n>=0
    time divisor factor = 2.0 if base_eut <= 16 else 2.8 (applied per OC)
    EU/t scales by 4^n
    min duration is 1 tick (ceil rounding)
    """
    mv = VOLTAGE_BY_TIER[tier]
    if base_eut <= 0:
        return OCResult(overclocks=0, ticks=max(1, math.ceil(base_time_s / TICK_S)), seconds=max(TICK_S, base_time_s), eut=0.0)
    if base_eut > mv:
        # Cannot run at this tier; treat as no OC but still round to ticks
        ticks = max(1, math.ceil(base_time_s / TICK_S))
        return OCResult(overclocks=0, ticks=ticks, seconds=ticks * TICK_S, eut=base_eut)

    n = int(math.floor(math.log(max(mv / base_eut, 1.0), 4)))
    n = max(0, n)

    factor = 2.0 if base_eut <= 16.0 else 2.8
    base_ticks = max(1, math.ceil(base_time_s / TICK_S))
    # Apply divisor per OC
    ticks_f = base_ticks / (factor ** n if n > 0 else 1.0)
    ticks = max(1, math.ceil(ticks_f))
    eut = base_eut * (4 ** n)
    return OCResult(overclocks=n, ticks=ticks, seconds=ticks * TICK_S, eut=eut)

