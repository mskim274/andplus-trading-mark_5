"""K-Hunter Trading System - Data Models"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class OrderSide(Enum):
    """주문 방향"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """주문 유형"""
    LIMIT = "00"           # 지정가
    MARKET = "01"          # 시장가
    CONDITIONAL = "02"     # 조건부지정가
    BEST_LIMIT = "03"      # 최유리지정가
    FIRST_LIMIT = "04"     # 최우선지정가
    BEFORE_MARKET = "05"   # 장전시간외
    AFTER_MARKET = "06"    # 장후시간외


class OrderStatus(Enum):
    """주문 상태"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class StockInfo:
    """종목 정보"""
    code: str
    name: str
    market: str = "KRX"  # KRX, KOSPI, KOSDAQ

    def __post_init__(self):
        # 종목코드 6자리 패딩
        self.code = self.code.zfill(6)


@dataclass
class Price:
    """가격 정보"""
    current: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    prev_close: float = 0.0
    change: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    """보유 포지션"""
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: float = 0.0
    sellable_qty: int = 0

    @property
    def total_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def profit_loss(self) -> float:
        return (self.current_price - self.avg_price) * self.quantity

    @property
    def profit_loss_rate(self) -> float:
        if self.avg_price == 0:
            return 0.0
        return ((self.current_price - self.avg_price) / self.avg_price) * 100


@dataclass
class Order:
    """주문 정보"""
    stock_code: str
    side: OrderSide
    quantity: int
    price: float
    order_type: OrderType = OrderType.LIMIT
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    filled_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    message: str = ""

    def __post_init__(self):
        self.stock_code = self.stock_code.zfill(6)


@dataclass
class AccountBalance:
    """계좌 잔고"""
    total_balance: float          # 총 평가금액
    cash_balance: float           # 예수금
    stock_balance: float          # 주식 평가금액
    total_profit_loss: float      # 총 손익
    total_profit_loss_rate: float # 총 손익률
    positions: List[Position] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def available_cash(self) -> float:
        """주문 가능 금액"""
        return self.cash_balance


@dataclass
class TradeSignal:
    """매매 신호"""
    stock_code: str
    stock_name: str
    side: OrderSide
    reason: str
    confidence: float  # 0.0 ~ 1.0
    suggested_price: float = 0.0
    suggested_qty: int = 0
    source: str = ""  # 신호 발생 소스 (예: "kiwoom_condition", "strategy")
    timestamp: datetime = field(default_factory=datetime.now)
