"""
K-Hunter Trading System - Position Manager
포지션 관리: 보유 종목 추적, 익절/손절 모니터링
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Callable
from loguru import logger

from src.core.models import Order, OrderSide, OrderStatus, Position
from src.core.events import Event, EventType, event_bus


@dataclass
class ManagedPosition:
    """관리 대상 포지션"""
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    entry_time: datetime
    entry_reason: str

    # 실시간 업데이트
    current_price: float = 0
    high_price: float = 0     # 진입 후 고점
    low_price: float = 0      # 진입 후 저점

    # 상태
    is_closing: bool = False  # 청산 진행 중
    close_order_id: str = ""

    @property
    def profit_loss(self) -> float:
        """손익금액"""
        return (self.current_price - self.avg_price) * self.quantity

    @property
    def profit_loss_rate(self) -> float:
        """손익률"""
        if self.avg_price <= 0:
            return 0
        return (self.current_price - self.avg_price) / self.avg_price

    @property
    def current_value(self) -> float:
        """현재 평가금액"""
        return self.current_price * self.quantity

    @property
    def hold_time_minutes(self) -> int:
        """보유 시간 (분)"""
        return int((datetime.now() - self.entry_time).total_seconds() / 60)


class PositionManager:
    """
    포지션 매니저

    역할:
    1. 보유 포지션 추적
    2. 실시간 가격 업데이트
    3. 익절/손절 조건 모니터링
    4. 청산 주문 실행 요청
    """

    def __init__(
        self,
        take_profit_pct: float = 0.05,
        stop_loss_pct: float = 0.02,
        trailing_stop_pct: float = 0.015,
        max_hold_minutes: int = 180
    ):
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.max_hold_minutes = max_hold_minutes

        # 포지션 관리
        self._positions: Dict[str, ManagedPosition] = {}

        # 콜백
        self._on_exit_signal: Optional[Callable] = None

        # 이벤트 구독
        self._setup_event_handlers()

        logger.info("Position Manager initialized")

    def _setup_event_handlers(self):
        """이벤트 핸들러 설정"""
        event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)
        event_bus.subscribe(EventType.KIWOOM_PRICE_UPDATE, self._on_price_update)

    # ==================== 포지션 관리 ====================

    def add_position(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        avg_price: float,
        reason: str = ""
    ) -> ManagedPosition:
        """
        포지션 추가

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            quantity: 수량
            avg_price: 평균단가
            reason: 진입 사유

        Returns:
            생성된 포지션
        """
        position = ManagedPosition(
            stock_code=stock_code,
            stock_name=stock_name,
            quantity=quantity,
            avg_price=avg_price,
            entry_time=datetime.now(),
            entry_reason=reason,
            current_price=avg_price,
            high_price=avg_price,
            low_price=avg_price
        )

        self._positions[stock_code] = position

        logger.info(
            f"Position opened: {stock_name}({stock_code}) "
            f"x{quantity} @ {avg_price:,.0f}"
        )

        # 이벤트 발행
        event_bus.publish(Event(
            type=EventType.POSITION_OPENED,
            data={
                "stock_code": stock_code,
                "stock_name": stock_name,
                "quantity": quantity,
                "avg_price": avg_price,
                "reason": reason
            },
            source="position_manager"
        ))

        return position

    def remove_position(self, stock_code: str, reason: str = ""):
        """포지션 제거"""
        position = self._positions.pop(stock_code, None)
        if position:
            logger.info(
                f"Position closed: {position.stock_name}({stock_code}) "
                f"P/L: {position.profit_loss:+,.0f} ({position.profit_loss_rate*100:+.2f}%) "
                f"Reason: {reason}"
            )

            # 이벤트 발행
            event_bus.publish(Event(
                type=EventType.POSITION_CLOSED,
                data={
                    "stock_code": stock_code,
                    "stock_name": position.stock_name,
                    "profit_loss": position.profit_loss,
                    "profit_loss_rate": position.profit_loss_rate,
                    "hold_time_minutes": position.hold_time_minutes,
                    "reason": reason
                },
                source="position_manager"
            ))

    def get_position(self, stock_code: str) -> Optional[ManagedPosition]:
        """포지션 조회"""
        return self._positions.get(stock_code)

    def has_position(self, stock_code: str) -> bool:
        """포지션 보유 여부 확인"""
        return stock_code in self._positions

    def get_all_positions(self) -> Dict[str, ManagedPosition]:
        """전체 포지션 조회"""
        return self._positions.copy()

    def sync_from_balance(self, positions: List[Position]):
        """
        계좌 잔고와 동기화

        Args:
            positions: 한투 API에서 조회한 포지션 목록
        """
        current_codes = set(self._positions.keys())
        new_codes = set(p.stock_code for p in positions)

        # 새로 추가된 포지션
        for pos in positions:
            if pos.stock_code not in self._positions:
                self.add_position(
                    stock_code=pos.stock_code,
                    stock_name=pos.stock_name,
                    quantity=pos.quantity,
                    avg_price=pos.avg_price,
                    reason="synced_from_balance"
                )
            else:
                # 기존 포지션 업데이트 (평균단가, 수량, 현재가)
                managed = self._positions[pos.stock_code]
                managed.avg_price = pos.avg_price  # 실제 체결 평균가로 업데이트
                managed.quantity = pos.quantity
                managed.current_price = pos.current_price

        # 없어진 포지션
        for code in current_codes - new_codes:
            self.remove_position(code, "synced_removed")

    # ==================== 가격 업데이트 ====================

    def update_price(self, stock_code: str, current_price: float):
        """
        실시간 가격 업데이트

        Args:
            stock_code: 종목코드
            current_price: 현재가
        """
        position = self._positions.get(stock_code)
        if not position or position.is_closing:
            return

        position.current_price = current_price

        # 고/저점 업데이트
        if current_price > position.high_price:
            position.high_price = current_price
        if current_price < position.low_price or position.low_price == 0:
            position.low_price = current_price

        # 청산 조건 체크
        exit_reason = self._check_exit_conditions(position)
        if exit_reason:
            self._trigger_exit(position, exit_reason)

    def _on_price_update(self, event: Event):
        """가격 업데이트 이벤트 처리"""
        stock_code = event.data.get("stock_code", "")
        current_price = event.data.get("current_price", 0)
        if stock_code and current_price > 0:
            self.update_price(stock_code, current_price)

    # ==================== 청산 조건 체크 ====================

    def _check_exit_conditions(self, position: ManagedPosition) -> Optional[str]:
        """
        청산 조건 체크

        Returns:
            청산 사유 (None이면 청산 안함)
        """
        if position.avg_price <= 0:
            return None

        profit_rate = position.profit_loss_rate

        # 1. 익절
        if profit_rate >= self.take_profit_pct:
            return f"take_profit (+{profit_rate*100:.1f}%)"

        # 2. 손절
        if profit_rate <= -self.stop_loss_pct:
            return f"stop_loss ({profit_rate*100:.1f}%)"

        # 3. 트레일링 스탑 (고점 대비 하락률)
        if position.high_price > position.avg_price:
            drawdown = (position.high_price - position.current_price) / position.high_price
            if drawdown >= self.trailing_stop_pct:
                return f"trailing_stop (-{drawdown*100:.1f}% from {position.high_price:,.0f})"

        # 4. 최대 보유 시간
        if position.hold_time_minutes >= self.max_hold_minutes:
            return f"max_hold_time ({position.hold_time_minutes}min)"

        return None

    def _trigger_exit(self, position: ManagedPosition, reason: str):
        """청산 트리거"""
        if position.is_closing:
            return

        position.is_closing = True

        logger.warning(
            f"EXIT TRIGGER: {position.stock_name}({position.stock_code}) "
            f"@ {position.current_price:,.0f} - {reason}"
        )

        # 청산 타입에 따른 이벤트
        if "take_profit" in reason:
            event_type = EventType.POSITION_TAKE_PROFIT
        elif "stop_loss" in reason:
            event_type = EventType.POSITION_STOP_LOSS
        else:
            event_type = EventType.STRATEGY_SELL_SIGNAL

        # 매도 신호 발행
        event_bus.publish(Event(
            type=event_type,
            data={
                "stock_code": position.stock_code,
                "stock_name": position.stock_name,
                "quantity": position.quantity,
                "current_price": position.current_price,
                "avg_price": position.avg_price,
                "profit_loss": position.profit_loss,
                "profit_loss_rate": position.profit_loss_rate,
                "reason": reason
            },
            source="position_manager"
        ))

        # 콜백 호출
        if self._on_exit_signal:
            self._on_exit_signal(position, reason)

    # ==================== 이벤트 핸들러 ====================

    def _on_order_filled(self, event: Event):
        """주문 체결 이벤트 처리"""
        stock_code = event.data.get("stock_code", "")
        side = event.data.get("side", "")
        quantity = event.data.get("filled_qty", 0)
        price = event.data.get("filled_price", 0)

        if side == "sell" and stock_code in self._positions:
            position = self._positions[stock_code]
            position.quantity -= quantity
            if position.quantity <= 0:
                self.remove_position(stock_code, "order_filled")

    # ==================== 콜백 설정 ====================

    def set_exit_callback(self, callback: Callable):
        """청산 신호 콜백 설정"""
        self._on_exit_signal = callback

    # ==================== 유틸리티 ====================

    def get_total_exposure(self) -> float:
        """총 포지션 가치"""
        return sum(p.current_value for p in self._positions.values())

    def get_total_profit_loss(self) -> float:
        """총 손익"""
        return sum(p.profit_loss for p in self._positions.values())

    def force_close_all(self, reason: str = "force_close"):
        """전체 포지션 강제 청산"""
        for stock_code in list(self._positions.keys()):
            position = self._positions[stock_code]
            if not position.is_closing:
                self._trigger_exit(position, reason)

    def print_summary(self):
        """포지션 요약 출력"""
        if not self._positions:
            logger.info("No active positions")
            return

        logger.info(f"\n{'='*60}")
        logger.info(f"POSITION SUMMARY ({len(self._positions)} positions)")
        logger.info(f"{'='*60}")

        total_value = 0
        total_pl = 0

        for code, pos in self._positions.items():
            total_value += pos.current_value
            total_pl += pos.profit_loss
            logger.info(
                f"  {pos.stock_name}({code}): {pos.quantity}주 "
                f"@ {pos.avg_price:,.0f} → {pos.current_price:,.0f} "
                f"({pos.profit_loss_rate*100:+.2f}%)"
            )

        logger.info(f"{'-'*60}")
        logger.info(f"Total Value: {total_value:,.0f}")
        logger.info(f"Total P/L: {total_pl:+,.0f}")
        logger.info(f"{'='*60}\n")
