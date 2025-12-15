"""
K-Hunter Trading System - Event System
이벤트 기반 컴포넌트 간 통신
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from queue import Queue
import threading
from loguru import logger


class EventType(Enum):
    """이벤트 타입"""
    # 키움 이벤트
    KIWOOM_CONDITION_RESULT = "kiwoom.condition.result"
    KIWOOM_REALTIME_IN = "kiwoom.realtime.in"       # 조건 편입
    KIWOOM_REALTIME_OUT = "kiwoom.realtime.out"     # 조건 이탈
    KIWOOM_PRICE_UPDATE = "kiwoom.price.update"

    # 한투 이벤트
    KIS_REALTIME_PRICE = "kis.realtime.price"       # 한투 실시간 체결가

    # 전략 이벤트
    STRATEGY_BUY_SIGNAL = "strategy.buy.signal"
    STRATEGY_SELL_SIGNAL = "strategy.sell.signal"

    # 주문 이벤트
    ORDER_SUBMITTED = "order.submitted"
    ORDER_FILLED = "order.filled"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_REJECTED = "order.rejected"

    # 포지션 이벤트
    POSITION_OPENED = "position.opened"
    POSITION_CLOSED = "position.closed"
    POSITION_TAKE_PROFIT = "position.take_profit"
    POSITION_STOP_LOSS = "position.stop_loss"

    # 시스템 이벤트
    SYSTEM_START = "system.start"
    SYSTEM_STOP = "system.stop"
    SYSTEM_ERROR = "system.error"


@dataclass
class Event:
    """이벤트 객체"""
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = ""

    def __repr__(self):
        return f"Event({self.type.value}, {self.data})"


class EventBus:
    """
    이벤트 버스 - Pub/Sub 패턴

    컴포넌트 간 느슨한 결합을 위한 이벤트 기반 통신
    """

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._queue: Queue = Queue()
        self._running = False
        self._lock = threading.Lock()

    def subscribe(self, event_type: EventType, callback: Callable[[Event], None]):
        """
        이벤트 구독

        Args:
            event_type: 구독할 이벤트 타입
            callback: 이벤트 발생시 호출될 콜백
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
            logger.debug(f"Subscribed to {event_type.value}")

    def unsubscribe(self, event_type: EventType, callback: Callable):
        """이벤트 구독 해제"""
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type].remove(callback)

    def publish(self, event: Event):
        """
        이벤트 발행 (동기)

        Args:
            event: 발행할 이벤트
        """
        with self._lock:
            subscribers = self._subscribers.get(event.type, [])

        for callback in subscribers:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")

    def publish_async(self, event: Event):
        """이벤트 발행 (비동기 큐)"""
        self._queue.put(event)

    def start_processing(self):
        """비동기 이벤트 처리 시작"""
        self._running = True
        while self._running:
            try:
                event = self._queue.get(timeout=0.1)
                self.publish(event)
            except:
                continue

    def stop_processing(self):
        """비동기 이벤트 처리 중지"""
        self._running = False


# 전역 이벤트 버스
event_bus = EventBus()
