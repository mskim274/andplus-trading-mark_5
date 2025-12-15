# Adapters Module
from .kis_adapter import KISAdapter

# Kiwoom requires 32-bit Python with PyQt5
try:
    from .kiwoom_adapter import (
        KiwoomAdapter,
        ConditionResult,
        RealtimeConditionSignal,
        StockPrice
    )
    KIWOOM_AVAILABLE = True
except ImportError:
    KIWOOM_AVAILABLE = False
    KiwoomAdapter = None
    ConditionResult = None
    RealtimeConditionSignal = None
    StockPrice = None

__all__ = [
    "KISAdapter",
    "KiwoomAdapter",
    "KIWOOM_AVAILABLE",
    "ConditionResult",
    "RealtimeConditionSignal",
    "StockPrice",
]
