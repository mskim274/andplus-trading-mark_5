"""
K-Hunter Trading System - Data Recorder
실시간 데이터 기록 담당 (이벤트 기반)
"""

from datetime import datetime, date
from typing import Optional, Dict, Any
from loguru import logger

from src.core.events import Event, EventType, event_bus
from src.data.models import TradeRecord, SignalRecord, TradeSide, SignalType, ActionResult
from src.data.repositories.trade_repository import trade_repository
from src.data.repositories.signal_repository import signal_repository
from src.data.repositories.daily_repository import daily_repository


class DataRecorder:
    """
    데이터 기록기

    이벤트를 구독하여 자동으로 데이터를 저장

    Recorded Events:
    - 조건검색 편입/이탈 → signals 테이블
    - 주문 체결 → trades 테이블
    - 매수/매도 결정 → signals 테이블 업데이트
    """

    def __init__(self):
        self._signal_id_map: Dict[str, int] = {}  # stock_code -> last signal_id
        self._trade_id_map: Dict[str, int] = {}   # stock_code -> buy trade_id (매도 시 연결용)
        self._starting_balance: int = 0
        self._is_recording = False

        logger.info("DataRecorder initialized")

    def start(self, starting_balance: int = 0):
        """기록 시작"""
        self._starting_balance = starting_balance
        self._is_recording = True
        self._setup_event_handlers()
        logger.info(f"DataRecorder started (starting_balance: {starting_balance:,})")

    def stop(self, ending_balance: int = 0):
        """
        기록 중지 및 일별 요약 생성

        Args:
            ending_balance: 종료 시점 잔고
        """
        self._is_recording = False

        # 오늘 일별 요약 생성
        try:
            daily_repository.calculate_and_save(
                trade_date=date.today(),
                starting_balance=self._starting_balance,
                ending_balance=ending_balance
            )
            logger.info("Daily summary saved")
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")

        logger.info("DataRecorder stopped")

    def _setup_event_handlers(self):
        """이벤트 핸들러 등록"""
        # 조건검색 시그널
        event_bus.subscribe(EventType.KIWOOM_REALTIME_IN, self._on_condition_in)
        event_bus.subscribe(EventType.KIWOOM_REALTIME_OUT, self._on_condition_out)

        # 매매 결정 (전략 시그널)
        event_bus.subscribe(EventType.STRATEGY_BUY_SIGNAL, self._on_buy_decision)

        # 주문 체결
        event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)

        # 포지션 청산
        event_bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)

    # ==================== 시그널 기록 ====================

    def _on_condition_in(self, event: Event):
        """조건검색 편입 기록"""
        if not self._is_recording:
            return

        try:
            stock_code = event.data.get("stock_code", "")
            stock_name = event.data.get("stock_name", "")
            condition_name = event.data.get("condition_name", "")
            current_price = event.data.get("current_price", 0)
            volume = event.data.get("volume", 0)
            change_rate = event.data.get("change_rate", 0.0)

            signal = signal_repository.save_in_signal(
                stock_code=stock_code,
                stock_name=stock_name,
                condition_name=condition_name,
                current_price=current_price,
                volume=volume,
                change_rate=change_rate
            )

            # 시그널 ID 저장 (나중에 액션 결과 업데이트용)
            self._signal_id_map[stock_code] = signal.id

            logger.debug(f"Signal IN recorded: {stock_code} (id={signal.id})")

        except Exception as e:
            logger.error(f"Failed to record IN signal: {e}")

    def _on_condition_out(self, event: Event):
        """조건검색 이탈 기록"""
        if not self._is_recording:
            return

        try:
            stock_code = event.data.get("stock_code", "")
            stock_name = event.data.get("stock_name", "")
            condition_name = event.data.get("condition_name", "")
            current_price = event.data.get("current_price", 0)

            signal_repository.save_out_signal(
                stock_code=stock_code,
                stock_name=stock_name,
                condition_name=condition_name,
                current_price=current_price
            )

            logger.debug(f"Signal OUT recorded: {stock_code}")

        except Exception as e:
            logger.error(f"Failed to record OUT signal: {e}")

    def _on_buy_decision(self, event: Event):
        """매수 결정 기록 (시그널 액션 결과 업데이트)"""
        if not self._is_recording:
            return

        try:
            stock_code = event.data.get("stock_code", "")
            signal_id = self._signal_id_map.get(stock_code)

            if signal_id:
                signal_repository.update_action(
                    signal_id=signal_id,
                    acted=True,
                    action_result=ActionResult.BUY
                )
                logger.debug(f"Signal action updated: {stock_code} -> BUY")

        except Exception as e:
            logger.error(f"Failed to update signal action: {e}")

    # ==================== 거래 기록 ====================

    def _on_order_filled(self, event: Event):
        """주문 체결 기록"""
        if not self._is_recording:
            return

        try:
            stock_code = event.data.get("stock_code", "")
            stock_name = event.data.get("stock_name", "")
            side = event.data.get("side", "")
            quantity = event.data.get("filled_qty", 0)
            price = event.data.get("filled_price", 0)
            condition_name = event.data.get("condition_name", "")
            strategy = event.data.get("strategy", "")

            if side.upper() == "BUY":
                trade = trade_repository.save_buy(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    quantity=quantity,
                    price=price,
                    condition_name=condition_name,
                    strategy=strategy
                )
                # 매수 거래 ID 저장 (매도 시 연결용)
                self._trade_id_map[stock_code] = trade.id

                logger.debug(f"BUY trade recorded: {stock_code} x{quantity} @ {price:,}")

            elif side.upper() == "SELL":
                # 매수 거래와 연결
                buy_trade_id = self._trade_id_map.pop(stock_code, None)
                buy_price = event.data.get("avg_price", 0)  # 평균 매수가

                trade = trade_repository.save_sell(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    quantity=quantity,
                    price=price,
                    buy_price=buy_price,
                    buy_trade_id=buy_trade_id,
                    condition_name=condition_name,
                    strategy=strategy
                )

                logger.debug(
                    f"SELL trade recorded: {stock_code} x{quantity} @ {price:,} "
                    f"(profit: {trade.profit:+,})"
                )

        except Exception as e:
            logger.error(f"Failed to record trade: {e}")

    def _on_position_closed(self, event: Event):
        """포지션 청산 기록 (추가 정보 로깅)"""
        if not self._is_recording:
            return

        stock_code = event.data.get("stock_code", "")
        profit_loss = event.data.get("profit_loss", 0)
        reason = event.data.get("reason", "")

        logger.info(f"Position closed: {stock_code}, P/L: {profit_loss:+,}, reason: {reason}")

    # ==================== 수동 기록 메서드 ====================

    def record_signal_skip(
        self,
        stock_code: str,
        skip_reason: str,
        action_result: ActionResult = ActionResult.SKIP
    ):
        """
        시그널 스킵 기록

        전략에서 필터링되어 매수하지 않은 경우 호출
        """
        if not self._is_recording:
            return

        signal_id = self._signal_id_map.get(stock_code)
        if signal_id:
            try:
                signal_repository.update_action(
                    signal_id=signal_id,
                    acted=False,
                    action_result=action_result,
                    skip_reason=skip_reason
                )
                logger.debug(f"Signal skip recorded: {stock_code} -> {skip_reason}")
            except Exception as e:
                logger.error(f"Failed to record signal skip: {e}")

    def record_trade_manually(
        self,
        stock_code: str,
        stock_name: str,
        side: str,
        quantity: int,
        price: int,
        **kwargs
    ) -> Optional[TradeRecord]:
        """
        수동 거래 기록

        API 체결 이벤트 없이 직접 기록할 때 사용
        """
        try:
            if side.upper() == "BUY":
                return trade_repository.save_buy(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    quantity=quantity,
                    price=price,
                    **kwargs
                )
            else:
                return trade_repository.save_sell(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    quantity=quantity,
                    price=price,
                    **kwargs
                )
        except Exception as e:
            logger.error(f"Failed to record trade manually: {e}")
            return None

    # ==================== 일별 요약 ====================

    def generate_daily_summary(self, trade_date: date = None, starting_balance: int = 0, ending_balance: int = 0):
        """일별 요약 생성 (수동)"""
        trade_date = trade_date or date.today()

        try:
            summary = daily_repository.calculate_and_save(
                trade_date=trade_date,
                starting_balance=starting_balance or self._starting_balance,
                ending_balance=ending_balance
            )
            logger.info(f"Daily summary generated: {trade_date} (profit: {summary.total_profit:+,})")
            return summary
        except Exception as e:
            logger.error(f"Failed to generate daily summary: {e}")
            return None

    # ==================== 상태 조회 ====================

    def get_today_stats(self) -> Dict[str, Any]:
        """오늘 통계 조회"""
        trade_stats = trade_repository.get_daily_stats(date.today())
        signal_stats = signal_repository.get_daily_stats(date.today())

        return {
            "trades": trade_stats,
            "signals": signal_stats,
        }


# 전역 인스턴스
data_recorder = DataRecorder()
