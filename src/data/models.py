"""
K-Hunter Trading System - Data Models
데이터베이스 저장용 모델 정의
"""

from dataclasses import dataclass, field
from datetime import date, time, datetime
from typing import Optional
from enum import Enum


class TradeSide(Enum):
    """거래 방향"""
    BUY = "BUY"
    SELL = "SELL"


class SignalType(Enum):
    """시그널 타입"""
    IN = "IN"      # 조건 편입
    OUT = "OUT"    # 조건 이탈


class ActionResult(Enum):
    """매매 결과"""
    BUY = "BUY"           # 매수 실행
    SELL = "SELL"         # 매도 실행
    SKIP = "SKIP"         # 스킵 (필터링)
    FILTERED = "FILTERED" # 전략 필터링
    ERROR = "ERROR"       # 에러 발생


@dataclass
class TradeRecord:
    """
    거래 기록 (체결된 주문)

    매수/매도 체결 시 저장
    """
    # 필수 필드
    trade_date: date
    trade_time: time
    stock_code: str
    side: TradeSide
    quantity: int
    price: int
    amount: int  # 체결금액 (price * quantity)

    # 선택 필드
    id: Optional[int] = None
    stock_name: Optional[str] = None
    fee: int = 0              # 수수료
    tax: int = 0              # 세금
    profit: Optional[int] = None       # 실현손익 (매도시)
    profit_rate: Optional[float] = None  # 수익률 (매도시)
    condition_name: Optional[str] = None  # 진입 조건검색명
    strategy: Optional[str] = None        # 전략명
    memo: Optional[str] = None
    created_at: Optional[datetime] = None

    # 매칭용 (매도 시 매수 거래와 연결)
    buy_trade_id: Optional[int] = None

    def __post_init__(self):
        if isinstance(self.side, str):
            self.side = TradeSide(self.side)
        if self.created_at is None:
            self.created_at = datetime.now()

    @property
    def net_amount(self) -> int:
        """순 거래금액 (수수료/세금 차감)"""
        if self.side == TradeSide.BUY:
            return self.amount + self.fee
        else:
            return self.amount - self.fee - self.tax

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {
            "id": self.id,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "trade_time": self.trade_time.isoformat() if self.trade_time else None,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "side": self.side.value if isinstance(self.side, TradeSide) else self.side,
            "quantity": self.quantity,
            "price": self.price,
            "amount": self.amount,
            "fee": self.fee,
            "tax": self.tax,
            "profit": self.profit,
            "profit_rate": self.profit_rate,
            "condition_name": self.condition_name,
            "strategy": self.strategy,
            "memo": self.memo,
            "buy_trade_id": self.buy_trade_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class SignalRecord:
    """
    조건검색 시그널 기록

    조건검색 편입/이탈 시 저장
    """
    # 필수 필드
    signal_date: date
    signal_time: time
    stock_code: str
    condition_name: str
    signal_type: SignalType  # IN/OUT

    # 선택 필드
    id: Optional[int] = None
    stock_name: Optional[str] = None
    current_price: Optional[int] = None
    volume: Optional[int] = None
    change_rate: Optional[float] = None

    # 액션 결과
    acted: bool = False           # 실제 매매 여부
    action_result: Optional[ActionResult] = None  # BUY/SKIP/FILTERED
    skip_reason: Optional[str] = None  # 스킵 사유

    created_at: Optional[datetime] = None

    def __post_init__(self):
        if isinstance(self.signal_type, str):
            self.signal_type = SignalType(self.signal_type)
        if isinstance(self.action_result, str):
            self.action_result = ActionResult(self.action_result)
        if self.created_at is None:
            self.created_at = datetime.now()

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {
            "id": self.id,
            "signal_date": self.signal_date.isoformat() if self.signal_date else None,
            "signal_time": self.signal_time.isoformat() if self.signal_time else None,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "condition_name": self.condition_name,
            "signal_type": self.signal_type.value if isinstance(self.signal_type, SignalType) else self.signal_type,
            "current_price": self.current_price,
            "volume": self.volume,
            "change_rate": self.change_rate,
            "acted": self.acted,
            "action_result": self.action_result.value if isinstance(self.action_result, ActionResult) else self.action_result,
            "skip_reason": self.skip_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class DailySummary:
    """
    일별 거래 요약

    장 마감 후 또는 정기적으로 집계
    """
    trade_date: date

    # 잔고
    starting_balance: int = 0      # 시작 잔고
    ending_balance: int = 0        # 종료 잔고

    # 손익
    total_profit: int = 0          # 당일 실현손익
    profit_rate: float = 0.0       # 당일 수익률 (%)

    # 거래 통계
    trade_count: int = 0           # 총 거래 횟수
    buy_count: int = 0             # 매수 횟수
    sell_count: int = 0            # 매도 횟수

    # 승패
    win_count: int = 0             # 수익 거래 수
    loss_count: int = 0            # 손실 거래 수
    even_count: int = 0            # 본전 거래 수

    # 수익/손실
    win_rate: float = 0.0          # 승률 (%)
    max_profit: int = 0            # 최대 수익
    max_loss: int = 0              # 최대 손실 (음수)
    avg_profit: float = 0.0        # 평균 수익
    avg_loss: float = 0.0          # 평균 손실

    # 시그널
    signal_count: int = 0          # 시그널 수
    signal_acted_count: int = 0    # 실행된 시그널 수

    # 메타
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    @property
    def profit_factor(self) -> float:
        """Profit Factor (총이익/총손실)"""
        total_loss = abs(self.avg_loss * self.loss_count) if self.loss_count > 0 else 0
        total_win = self.avg_profit * self.win_count if self.win_count > 0 else 0
        if total_loss == 0:
            return float('inf') if total_win > 0 else 0.0
        return total_win / total_loss

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {
            "id": self.id,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "starting_balance": self.starting_balance,
            "ending_balance": self.ending_balance,
            "total_profit": self.total_profit,
            "profit_rate": self.profit_rate,
            "trade_count": self.trade_count,
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "even_count": self.even_count,
            "win_rate": self.win_rate,
            "max_profit": self.max_profit,
            "max_loss": self.max_loss,
            "avg_profit": self.avg_profit,
            "avg_loss": self.avg_loss,
            "signal_count": self.signal_count,
            "signal_acted_count": self.signal_acted_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class PositionSnapshot:
    """
    포지션 스냅샷

    정기적으로 현재 포지션 상태 저장 (시계열 분석용)
    """
    snapshot_time: datetime
    stock_code: str

    # 포지션 정보
    quantity: int
    avg_price: int              # 평균 매수가
    current_price: int = 0      # 현재가

    # 평가
    eval_amount: int = 0        # 평가금액
    profit: int = 0             # 평가손익
    profit_rate: float = 0.0    # 수익률 (%)

    # 메타
    id: Optional[int] = None
    stock_name: Optional[str] = None

    def __post_init__(self):
        if self.current_price > 0 and self.quantity > 0:
            self.eval_amount = self.current_price * self.quantity
            cost = self.avg_price * self.quantity
            self.profit = self.eval_amount - cost
            self.profit_rate = (self.profit / cost * 100) if cost > 0 else 0.0

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {
            "id": self.id,
            "snapshot_time": self.snapshot_time.isoformat() if self.snapshot_time else None,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "quantity": self.quantity,
            "avg_price": self.avg_price,
            "current_price": self.current_price,
            "eval_amount": self.eval_amount,
            "profit": self.profit,
            "profit_rate": self.profit_rate,
        }
