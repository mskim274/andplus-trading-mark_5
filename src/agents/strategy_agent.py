"""
K-Hunter Trading System - Strategy Agent
전략 엔진: 2차 필터링 + 매매 신호 생성 + 자금 관리
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from enum import Enum
from loguru import logger

from src.core.models import OrderSide, TradeSignal
from src.core.events import Event, EventType, event_bus


class FilterResult(Enum):
    """필터 결과"""
    PASS = "pass"
    REJECT_DUPLICATE = "reject_duplicate"
    REJECT_COOLDOWN = "reject_cooldown"
    REJECT_MAX_POSITIONS = "reject_max_positions"
    REJECT_MAX_EXPOSURE = "reject_max_exposure"
    REJECT_MIN_AMOUNT = "reject_min_amount"
    REJECT_BLACKLIST = "reject_blacklist"


@dataclass
class StrategyConfig:
    """전략 설정"""
    # 자금 관리
    max_position_per_stock: float = 0.10    # 종목당 최대 비중 10%
    max_total_exposure: float = 0.50        # 총 노출 최대 50%
    min_order_amount: int = 100000          # 최소 주문 금액 10만원
    max_positions: int = 5                  # 최대 보유 종목 수

    # 진입 조건
    entry_cooldown_minutes: int = 30        # 동일 종목 재진입 대기 시간
    allow_duplicate_condition: bool = False  # 동일 조건 중복 진입 허용

    # 청산 조건
    take_profit_pct: float = 0.05           # 익절 +5%
    stop_loss_pct: float = 0.02             # 손절 -2%
    trailing_stop_pct: float = 0.015        # 트레일링 스탑 1.5%
    max_hold_minutes: int = 180             # 최대 보유 시간 3시간

    # 블랙리스트
    blacklist: Set[str] = field(default_factory=set)


@dataclass
class SignalRecord:
    """신호 기록"""
    stock_code: str
    condition_name: str
    signal_type: str  # "IN" or "OUT"
    timestamp: datetime
    processed: bool = False


class StrategyAgent:
    """
    전략 에이전트

    역할:
    1. 키움 조건검색 신호 수신
    2. 2차 필터링 (중복, 자금, 블랙리스트)
    3. 매수/매도 신호 생성
    4. 자금 관리 규칙 적용
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()

        # 상태 관리
        self._signal_history: List[SignalRecord] = []
        self._active_positions: Dict[str, dict] = {}  # code -> position info
        self._pending_signals: List[TradeSignal] = []
        self._last_entry_time: Dict[str, datetime] = {}  # code -> last entry time

        # 계좌 정보 (외부에서 업데이트)
        self._total_balance: float = 0
        self._available_cash: float = 0

        # 이벤트 구독
        self._setup_event_handlers()

        logger.info("Strategy Agent initialized")

    def _setup_event_handlers(self):
        """이벤트 핸들러 설정"""
        event_bus.subscribe(EventType.KIWOOM_REALTIME_IN, self._on_condition_in)
        event_bus.subscribe(EventType.KIWOOM_REALTIME_OUT, self._on_condition_out)
        event_bus.subscribe(EventType.KIWOOM_CONDITION_RESULT, self._on_condition_result)

    # ==================== 계좌 정보 업데이트 ====================

    def update_balance(self, total_balance: float, available_cash: float):
        """계좌 잔고 업데이트"""
        self._total_balance = total_balance
        self._available_cash = available_cash

    def update_positions(self, positions: Dict[str, dict]):
        """보유 포지션 업데이트"""
        self._active_positions = positions

    # ==================== 이벤트 핸들러 ====================

    def _on_condition_in(self, event: Event):
        """조건 편입 이벤트 처리"""
        stock_code = event.data.get("stock_code", "")
        stock_name = event.data.get("stock_name", "")
        condition_name = event.data.get("condition_name", "")

        logger.info(f"[IN] {stock_name}({stock_code}) - {condition_name}")

        # 신호 기록
        record = SignalRecord(
            stock_code=stock_code,
            condition_name=condition_name,
            signal_type="IN",
            timestamp=datetime.now()
        )
        self._signal_history.append(record)

        # 매수 신호 생성 시도
        self._process_buy_signal(stock_code, stock_name, condition_name)

    def _on_condition_out(self, event: Event):
        """조건 이탈 이벤트 처리"""
        stock_code = event.data.get("stock_code", "")
        stock_name = event.data.get("stock_name", "")
        condition_name = event.data.get("condition_name", "")

        logger.info(f"[OUT] {stock_name}({stock_code}) - {condition_name}")

        # 신호 기록
        record = SignalRecord(
            stock_code=stock_code,
            condition_name=condition_name,
            signal_type="OUT",
            timestamp=datetime.now()
        )
        self._signal_history.append(record)

        # 보유 중이면 매도 신호
        if stock_code in self._active_positions:
            self._process_sell_signal(stock_code, stock_name, "condition_out")

    def _on_condition_result(self, event: Event):
        """조건검색 결과 처리 (초기 목록)"""
        condition_name = event.data.get("condition_name", "")
        stock_codes = event.data.get("stock_codes", [])

        logger.info(f"Condition result: {condition_name} -> {len(stock_codes)} stocks")

        # 초기 목록은 신호로 처리하지 않음 (실시간만 처리)
        # 필요시 여기서 bulk 처리 가능

    # ==================== 거래량 급등 신호 ====================

    def on_volume_surge(self, stock_code: str, stock_name: str, surge_reason: str):
        """
        거래량 급등 신호 처리 (2차 필터 통과)

        MainController에서 호출됨
        """
        logger.info(f"[VOLUME SURGE] {stock_name}({stock_code}) - {surge_reason}")

        # 2차 필터링 (중복, 자금 등)
        filter_result = self._apply_filters(stock_code, "volume_surge")

        if filter_result != FilterResult.PASS:
            logger.info(f"Volume surge filtered: {stock_code} - {filter_result.value}")
            return

        # 주문 수량/금액 계산
        order_amount, order_qty = self._calculate_order_size(stock_code)

        if order_qty <= 0:
            logger.info(f"Volume surge rejected: {stock_code} - insufficient quantity")
            return

        # 매수 신호 생성
        signal = TradeSignal(
            stock_code=stock_code,
            stock_name=stock_name,
            side=OrderSide.BUY,
            reason=f"거래량급등: {surge_reason}",
            confidence=0.85,
            suggested_qty=order_qty,
            source="volume_surge"
        )

        logger.info(f"BUY SIGNAL (VOLUME): {stock_name}({stock_code}) x{order_qty}")

        # 이벤트 발행 → MainController._on_buy_signal() 호출됨
        event_bus.publish(Event(
            type=EventType.STRATEGY_BUY_SIGNAL,
            data={
                "signal": signal,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "quantity": order_qty,
                "reason": signal.reason
            },
            source="strategy_agent"
        ))

        # 진입 시간 기록
        self._last_entry_time[stock_code] = datetime.now()

    # ==================== 신호 처리 ====================

    def _process_buy_signal(self, stock_code: str, stock_name: str, condition_name: str):
        """매수 신호 처리"""
        # 2차 필터링
        filter_result = self._apply_filters(stock_code, condition_name)

        if filter_result != FilterResult.PASS:
            logger.info(f"Buy signal filtered: {stock_code} - {filter_result.value}")
            return

        # 주문 수량/금액 계산
        order_amount, order_qty = self._calculate_order_size(stock_code)

        if order_qty <= 0:
            logger.info(f"Buy signal rejected: {stock_code} - insufficient quantity")
            return

        # 매수 신호 생성
        signal = TradeSignal(
            stock_code=stock_code,
            stock_name=stock_name,
            side=OrderSide.BUY,
            reason=f"조건편입: {condition_name}",
            confidence=0.8,
            suggested_qty=order_qty,
            source="kiwoom_condition"
        )

        logger.info(f"BUY SIGNAL: {stock_name}({stock_code}) x{order_qty}")

        # 이벤트 발행
        event_bus.publish(Event(
            type=EventType.STRATEGY_BUY_SIGNAL,
            data={
                "signal": signal,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "quantity": order_qty,
                "reason": signal.reason
            },
            source="strategy_agent"
        ))

        # 진입 시간 기록
        self._last_entry_time[stock_code] = datetime.now()

    def _process_sell_signal(self, stock_code: str, stock_name: str, reason: str):
        """매도 신호 처리"""
        position = self._active_positions.get(stock_code)
        if not position:
            return

        quantity = position.get("quantity", 0)
        if quantity <= 0:
            return

        # 매도 신호 생성
        signal = TradeSignal(
            stock_code=stock_code,
            stock_name=stock_name,
            side=OrderSide.SELL,
            reason=reason,
            confidence=0.9,
            suggested_qty=quantity,
            source="strategy_agent"
        )

        logger.info(f"SELL SIGNAL: {stock_name}({stock_code}) x{quantity} - {reason}")

        # 이벤트 발행
        event_bus.publish(Event(
            type=EventType.STRATEGY_SELL_SIGNAL,
            data={
                "signal": signal,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "quantity": quantity,
                "reason": reason
            },
            source="strategy_agent"
        ))

    # ==================== 2차 필터링 ====================

    def _apply_filters(self, stock_code: str, condition_name: str) -> FilterResult:
        """
        2차 필터링 적용

        Returns:
            FilterResult: 필터 통과 여부
        """
        # 1. 블랙리스트 체크
        if stock_code in self.config.blacklist:
            return FilterResult.REJECT_BLACKLIST

        # 2. 이미 보유 중인 종목
        if stock_code in self._active_positions:
            if not self.config.allow_duplicate_condition:
                return FilterResult.REJECT_DUPLICATE

        # 3. 쿨다운 체크 (동일 종목 재진입 대기)
        last_entry = self._last_entry_time.get(stock_code)
        if last_entry:
            cooldown = timedelta(minutes=self.config.entry_cooldown_minutes)
            if datetime.now() - last_entry < cooldown:
                return FilterResult.REJECT_COOLDOWN

        # 4. 최대 보유 종목 수 체크
        if len(self._active_positions) >= self.config.max_positions:
            return FilterResult.REJECT_MAX_POSITIONS

        # 5. 총 노출 한도 체크
        current_exposure = self._calculate_current_exposure()
        if current_exposure >= self.config.max_total_exposure:
            return FilterResult.REJECT_MAX_EXPOSURE

        return FilterResult.PASS

    def _calculate_current_exposure(self) -> float:
        """현재 총 노출 비율 계산"""
        if self._total_balance <= 0:
            return 0

        total_position_value = sum(
            p.get("current_value", 0)
            for p in self._active_positions.values()
        )

        return total_position_value / self._total_balance

    # ==================== 자금 관리 ====================

    def _calculate_order_size(self, stock_code: str, current_price: float = 0) -> tuple:
        """
        주문 수량 계산

        Args:
            stock_code: 종목코드
            current_price: 현재가 (0이면 외부에서 조회 필요)

        Returns:
            (주문금액, 주문수량)
        """
        logger.debug(f"_calculate_order_size: balance={self._total_balance}, cash={self._available_cash}, price={current_price}")

        if self._total_balance <= 0 or self._available_cash <= 0:
            logger.debug(f"_calculate_order_size: balance/cash is 0")
            return 0, 0

        # 종목당 최대 금액
        max_per_stock = self._total_balance * self.config.max_position_per_stock

        # 사용 가능한 금액 (여유 현금과 종목당 최대 중 작은 값)
        available_for_order = min(self._available_cash, max_per_stock)

        # 최소 주문 금액 체크
        if available_for_order < self.config.min_order_amount:
            logger.debug(f"_calculate_order_size: available={available_for_order} < min={self.config.min_order_amount}")
            return 0, 0

        # 현재가가 없으면 임시 수량 1 반환 (실제 수량은 MainController에서 재계산)
        # MainController._on_buy_signal에서 현재가 조회 후 수량 재계산함
        if current_price <= 0:
            logger.info(f"Order size: amount={available_for_order:,.0f} (quantity TBD)")
            return available_for_order, 1  # 임시 수량 1 반환

        # 수량 계산
        quantity = int(available_for_order / current_price)

        # 최소 1주
        if quantity < 1:
            return 0, 0

        actual_amount = quantity * current_price
        return actual_amount, quantity

    # ==================== 청산 조건 체크 ====================

    def check_exit_conditions(self, stock_code: str, current_price: float) -> Optional[str]:
        """
        청산 조건 체크

        Args:
            stock_code: 종목코드
            current_price: 현재가

        Returns:
            청산 사유 (None이면 청산하지 않음)
        """
        position = self._active_positions.get(stock_code)
        if not position:
            return None

        avg_price = position.get("avg_price", 0)
        if avg_price <= 0:
            return None

        profit_rate = (current_price - avg_price) / avg_price

        # 익절
        if profit_rate >= self.config.take_profit_pct:
            return f"take_profit (+{profit_rate*100:.1f}%)"

        # 손절
        if profit_rate <= -self.config.stop_loss_pct:
            return f"stop_loss ({profit_rate*100:.1f}%)"

        # 트레일링 스탑 (고점 대비)
        high_price = position.get("high_price", current_price)
        if high_price > avg_price:
            drawdown = (high_price - current_price) / high_price
            if drawdown >= self.config.trailing_stop_pct:
                return f"trailing_stop (-{drawdown*100:.1f}% from high)"

        # 최대 보유 시간
        entry_time = position.get("entry_time")
        if entry_time:
            hold_time = datetime.now() - entry_time
            if hold_time > timedelta(minutes=self.config.max_hold_minutes):
                return f"max_hold_time ({hold_time})"

        return None

    def update_position_high(self, stock_code: str, current_price: float):
        """포지션 고점 업데이트 (트레일링 스탑용)"""
        if stock_code in self._active_positions:
            position = self._active_positions[stock_code]
            high_price = position.get("high_price", 0)
            if current_price > high_price:
                position["high_price"] = current_price

    # ==================== 상태 조회 ====================

    def get_signal_history(self, limit: int = 50) -> List[SignalRecord]:
        """최근 신호 기록 조회"""
        return self._signal_history[-limit:]

    def get_active_positions(self) -> Dict[str, dict]:
        """활성 포지션 조회"""
        return self._active_positions.copy()

    def add_to_blacklist(self, stock_code: str):
        """블랙리스트 추가"""
        self.config.blacklist.add(stock_code)
        logger.info(f"Added to blacklist: {stock_code}")

    def remove_from_blacklist(self, stock_code: str):
        """블랙리스트 제거"""
        self.config.blacklist.discard(stock_code)
