"""
K-Hunter Trading System - TR Monitor
KIS 및 Kiwoom API 호출 모니터링
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from collections import deque
from enum import Enum
import threading
from loguru import logger


class TRSource(Enum):
    """TR 소스"""
    KIS = "kis"
    KIWOOM = "kiwoom"


class TRType(Enum):
    """TR 타입"""
    # KIS
    KIS_TOKEN = "kis_token"
    KIS_BALANCE = "kis_balance"
    KIS_ORDER_BUY = "kis_order_buy"
    KIS_ORDER_SELL = "kis_order_sell"
    KIS_PRICE = "kis_price"
    KIS_APPROVAL = "kis_approval"
    KIS_OTHER = "kis_other"

    # Kiwoom
    KIWOOM_CONDITION_LIST = "kiwoom_condition_list"
    KIWOOM_CONDITION_SEARCH = "kiwoom_condition_search"
    KIWOOM_REALTIME_SUBSCRIBE = "kiwoom_realtime_subscribe"
    KIWOOM_OTHER = "kiwoom_other"


@dataclass
class TRRecord:
    """TR 호출 기록"""
    timestamp: datetime
    source: TRSource
    tr_type: TRType
    tr_name: str
    success: bool
    response_time_ms: float = 0.0
    error_message: str = ""
    details: Dict = field(default_factory=dict)


class TRMonitor:
    """
    TR 모니터링 시스템

    Features:
    - 실시간 TR 호출 카운트
    - 분당/초당 호출 빈도 추적
    - 에러율 계산
    - 호출 이력 관리
    """

    # KIS Rate Limits
    KIS_RATE_LIMIT_PER_SECOND = 20

    def __init__(self, history_size: int = 1000):
        self._lock = threading.Lock()

        # 호출 이력 (최근 N개)
        self._history: deque = deque(maxlen=history_size)

        # 초당 카운트 (최근 60초)
        self._kis_per_second: deque = deque(maxlen=60)
        self._kiwoom_per_second: deque = deque(maxlen=60)

        # 통계
        self._stats = {
            TRSource.KIS: {
                "total_calls": 0,
                "success_count": 0,
                "error_count": 0,
                "total_response_time": 0.0,
                "calls_this_second": 0,
                "last_second": datetime.now().second,
                "by_type": {},
            },
            TRSource.KIWOOM: {
                "total_calls": 0,
                "success_count": 0,
                "error_count": 0,
                "total_response_time": 0.0,
                "calls_this_second": 0,
                "last_second": datetime.now().second,
                "by_type": {},
            }
        }

        # 콜백 (UI 업데이트용)
        self._on_update_callbacks: List[Callable] = []

        logger.info("TRMonitor initialized")

    def record(
        self,
        source: TRSource,
        tr_type: TRType,
        tr_name: str,
        success: bool,
        response_time_ms: float = 0.0,
        error_message: str = "",
        details: Dict = None
    ):
        """
        TR 호출 기록

        Args:
            source: KIS or KIWOOM
            tr_type: TR 타입
            tr_name: TR 이름/설명
            success: 성공 여부
            response_time_ms: 응답 시간 (ms)
            error_message: 에러 메시지
            details: 추가 상세 정보
        """
        record = TRRecord(
            timestamp=datetime.now(),
            source=source,
            tr_type=tr_type,
            tr_name=tr_name,
            success=success,
            response_time_ms=response_time_ms,
            error_message=error_message,
            details=details or {}
        )

        with self._lock:
            # 이력 추가
            self._history.append(record)

            # 통계 업데이트
            stats = self._stats[source]
            stats["total_calls"] += 1

            if success:
                stats["success_count"] += 1
            else:
                stats["error_count"] += 1

            stats["total_response_time"] += response_time_ms

            # 타입별 카운트
            type_key = tr_type.value
            if type_key not in stats["by_type"]:
                stats["by_type"][type_key] = {"count": 0, "errors": 0}
            stats["by_type"][type_key]["count"] += 1
            if not success:
                stats["by_type"][type_key]["errors"] += 1

            # 초당 카운트 업데이트
            current_second = datetime.now().second
            if current_second != stats["last_second"]:
                # 새로운 초 - 이전 초 데이터 저장
                if source == TRSource.KIS:
                    self._kis_per_second.append(stats["calls_this_second"])
                else:
                    self._kiwoom_per_second.append(stats["calls_this_second"])
                stats["calls_this_second"] = 0
                stats["last_second"] = current_second

            stats["calls_this_second"] += 1

        # 콜백 호출
        self._notify_update(record)

        # 로깅
        status = "✓" if success else "✗"
        logger.debug(f"TR[{source.value}] {status} {tr_name} ({response_time_ms:.0f}ms)")

    def get_kis_stats(self) -> Dict:
        """KIS 통계 조회"""
        with self._lock:
            stats = self._stats[TRSource.KIS].copy()
            stats["calls_per_second"] = stats["calls_this_second"]
            stats["rate_limit"] = self.KIS_RATE_LIMIT_PER_SECOND
            stats["rate_usage_pct"] = (stats["calls_this_second"] / self.KIS_RATE_LIMIT_PER_SECOND) * 100

            if stats["total_calls"] > 0:
                stats["error_rate"] = (stats["error_count"] / stats["total_calls"]) * 100
                stats["avg_response_time"] = stats["total_response_time"] / stats["total_calls"]
            else:
                stats["error_rate"] = 0.0
                stats["avg_response_time"] = 0.0

            return stats

    def get_kiwoom_stats(self) -> Dict:
        """Kiwoom 통계 조회"""
        with self._lock:
            stats = self._stats[TRSource.KIWOOM].copy()
            stats["calls_per_second"] = stats["calls_this_second"]

            if stats["total_calls"] > 0:
                stats["error_rate"] = (stats["error_count"] / stats["total_calls"]) * 100
                stats["avg_response_time"] = stats["total_response_time"] / stats["total_calls"]
            else:
                stats["error_rate"] = 0.0
                stats["avg_response_time"] = 0.0

            return stats

    def get_recent_history(self, count: int = 100, source: Optional[TRSource] = None) -> List[TRRecord]:
        """최근 호출 이력 조회"""
        with self._lock:
            if source:
                filtered = [r for r in self._history if r.source == source]
                return list(filtered)[-count:]
            return list(self._history)[-count:]

    def get_summary(self) -> Dict:
        """전체 요약 조회"""
        kis = self.get_kis_stats()
        kiwoom = self.get_kiwoom_stats()

        return {
            "kis": kis,
            "kiwoom": kiwoom,
            "total_calls": kis["total_calls"] + kiwoom["total_calls"],
            "total_errors": kis["error_count"] + kiwoom["error_count"],
        }

    def register_callback(self, callback: Callable[[TRRecord], None]):
        """업데이트 콜백 등록"""
        self._on_update_callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """콜백 해제"""
        if callback in self._on_update_callbacks:
            self._on_update_callbacks.remove(callback)

    def _notify_update(self, record: TRRecord):
        """콜백 호출"""
        for callback in self._on_update_callbacks:
            try:
                callback(record)
            except Exception as e:
                logger.error(f"TR Monitor callback error: {e}")

    def reset_stats(self):
        """통계 초기화"""
        with self._lock:
            for source in [TRSource.KIS, TRSource.KIWOOM]:
                self._stats[source] = {
                    "total_calls": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "total_response_time": 0.0,
                    "calls_this_second": 0,
                    "last_second": datetime.now().second,
                    "by_type": {},
                }
            self._history.clear()
            self._kis_per_second.clear()
            self._kiwoom_per_second.clear()
        logger.info("TR Monitor stats reset")


# 전역 TR 모니터 인스턴스
tr_monitor = TRMonitor()
