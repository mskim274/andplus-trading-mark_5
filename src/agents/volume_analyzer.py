"""
실시간 거래량 분석기
1분봉 거래량 급등 감지 및 체결강도 분석
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable
from loguru import logger


@dataclass
class VolumeData:
    """거래량 데이터"""
    stock_code: str
    timestamp: datetime
    volume: int
    price: int
    strength: float  # 체결강도


@dataclass
class MinuteBar:
    """1분봉 데이터"""
    timestamp: datetime
    open_price: int = 0
    high_price: int = 0
    low_price: int = 0
    close_price: int = 0
    volume: int = 0
    tick_count: int = 0


@dataclass
class VolumeAnalysisResult:
    """거래량 분석 결과"""
    stock_code: str
    stock_name: str
    current_volume: int          # 현재 1분 거래량
    avg_volume: int              # 평균 1분 거래량
    volume_ratio: float          # 거래량 배수
    strength: float              # 체결강도
    is_surge: bool               # 급등 여부
    surge_reason: str            # 급등 사유
    timestamp: datetime = field(default_factory=datetime.now)


class VolumeAnalyzer:
    """
    실시간 거래량 분석기

    기능:
    - 1분봉 거래량 집계
    - 평균 거래량 대비 급등 감지
    - 체결강도 분석
    """

    def __init__(
        self,
        volume_surge_ratio: float = 3.0,      # 거래량 급등 기준 (배수)
        strength_threshold: float = 120.0,     # 체결강도 기준 (%)
        lookback_minutes: int = 20,            # 평균 계산용 과거 분봉 수
        min_volume_threshold: int = 1000,      # 최소 거래량 (너무 적은 거래량 무시)
    ):
        self.volume_surge_ratio = volume_surge_ratio
        self.strength_threshold = strength_threshold
        self.lookback_minutes = lookback_minutes
        self.min_volume_threshold = min_volume_threshold

        # 종목별 1분봉 데이터 저장 (최근 N분)
        self._minute_bars: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=lookback_minutes + 1)
        )

        # 현재 진행 중인 1분봉
        self._current_bars: Dict[str, MinuteBar] = {}

        # 종목별 최근 체결강도
        self._last_strength: Dict[str, float] = {}

        # 종목명 매핑
        self._stock_names: Dict[str, str] = {}

        # 급등 감지 콜백
        self._on_surge_detected: Optional[Callable[[VolumeAnalysisResult], None]] = None

        logger.info(f"VolumeAnalyzer initialized: surge_ratio={volume_surge_ratio}x, "
                   f"strength={strength_threshold}%, lookback={lookback_minutes}min")

    def set_stock_name(self, stock_code: str, stock_name: str):
        """종목명 설정"""
        self._stock_names[stock_code] = stock_name

    def set_surge_callback(self, callback: Callable[[VolumeAnalysisResult], None]):
        """급등 감지 콜백 설정"""
        self._on_surge_detected = callback

    def update(self, stock_code: str, price: int, volume: int, strength: float) -> Optional[VolumeAnalysisResult]:
        """
        실시간 체결 데이터 업데이트

        Args:
            stock_code: 종목코드
            price: 현재가
            volume: 체결량 (누적 아님, 이번 체결 거래량)
            strength: 체결강도

        Returns:
            급등 감지 시 분석 결과, 아니면 None
        """
        now = datetime.now()
        current_minute = now.replace(second=0, microsecond=0)

        # 체결강도 저장
        self._last_strength[stock_code] = strength

        # 현재 진행 중인 1분봉 가져오기
        if stock_code not in self._current_bars:
            self._current_bars[stock_code] = MinuteBar(
                timestamp=current_minute,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                volume=0,
                tick_count=0
            )

        current_bar = self._current_bars[stock_code]

        # 새로운 분봉 시작
        if current_bar.timestamp < current_minute:
            # 이전 분봉 저장
            if current_bar.tick_count > 0:
                self._minute_bars[stock_code].append(current_bar)

            # 새 분봉 시작
            current_bar = MinuteBar(
                timestamp=current_minute,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                volume=0,
                tick_count=0
            )
            self._current_bars[stock_code] = current_bar

        # 현재 분봉 업데이트
        current_bar.high_price = max(current_bar.high_price, price)
        current_bar.low_price = min(current_bar.low_price, price)
        current_bar.close_price = price
        current_bar.volume += volume
        current_bar.tick_count += 1

        # 급등 체크
        return self._check_surge(stock_code)

    def _check_surge(self, stock_code: str) -> Optional[VolumeAnalysisResult]:
        """거래량 급등 체크"""
        if stock_code not in self._current_bars:
            return None

        current_bar = self._current_bars[stock_code]
        past_bars = list(self._minute_bars[stock_code])

        # 체결강도 (즉시 판단 가능)
        strength = self._last_strength.get(stock_code, 100.0)
        is_strength_high = strength >= self.strength_threshold

        # 거래량 배수 계산 (과거 데이터 필요)
        volume_ratio = 0.0
        avg_volume = 0.0
        is_volume_surge = False

        if len(past_bars) >= 1 and current_bar.volume >= self.min_volume_threshold:
            avg_volume = sum(bar.volume for bar in past_bars) / len(past_bars)
            if avg_volume > 0:
                volume_ratio = current_bar.volume / avg_volume
                is_volume_surge = volume_ratio >= self.volume_surge_ratio

        # 급등 여부 및 사유
        is_surge = False
        surge_reasons = []

        if is_volume_surge:
            is_surge = True
            surge_reasons.append(f"거래량 {volume_ratio:.1f}배")

        if is_strength_high:
            is_surge = True
            surge_reasons.append(f"체결강도 {strength:.0f}%")

        if not is_surge:
            return None

        result = VolumeAnalysisResult(
            stock_code=stock_code,
            stock_name=self._stock_names.get(stock_code, ""),
            current_volume=current_bar.volume,
            avg_volume=int(avg_volume),
            volume_ratio=volume_ratio,
            strength=strength,
            is_surge=True,
            surge_reason=" + ".join(surge_reasons)
        )

        logger.info(f"[SURGE] {result.stock_name}({stock_code}): {result.surge_reason}")

        # 콜백 호출
        if self._on_surge_detected:
            self._on_surge_detected(result)

        return result

    def get_analysis(self, stock_code: str) -> Optional[VolumeAnalysisResult]:
        """현재 분석 결과 조회"""
        if stock_code not in self._current_bars:
            return None

        current_bar = self._current_bars[stock_code]
        past_bars = list(self._minute_bars[stock_code])

        if len(past_bars) < 1:
            avg_volume = 0
            volume_ratio = 0
        else:
            avg_volume = sum(bar.volume for bar in past_bars) / len(past_bars)
            volume_ratio = current_bar.volume / avg_volume if avg_volume > 0 else 0

        strength = self._last_strength.get(stock_code, 100.0)

        is_surge = (volume_ratio >= self.volume_surge_ratio or
                   strength >= self.strength_threshold)

        surge_reasons = []
        if volume_ratio >= self.volume_surge_ratio:
            surge_reasons.append(f"거래량 {volume_ratio:.1f}배")
        if strength >= self.strength_threshold:
            surge_reasons.append(f"체결강도 {strength:.0f}%")

        return VolumeAnalysisResult(
            stock_code=stock_code,
            stock_name=self._stock_names.get(stock_code, ""),
            current_volume=current_bar.volume,
            avg_volume=int(avg_volume),
            volume_ratio=volume_ratio,
            strength=strength,
            is_surge=is_surge,
            surge_reason=" + ".join(surge_reasons) if surge_reasons else "-"
        )

    def clear(self, stock_code: str = None):
        """데이터 초기화"""
        if stock_code:
            if stock_code in self._minute_bars:
                self._minute_bars[stock_code].clear()
            if stock_code in self._current_bars:
                del self._current_bars[stock_code]
        else:
            self._minute_bars.clear()
            self._current_bars.clear()
            self._last_strength.clear()
