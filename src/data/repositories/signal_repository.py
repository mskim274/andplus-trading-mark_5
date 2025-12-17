"""
K-Hunter Trading System - Signal Repository
조건검색 시그널 저장/조회
"""

from datetime import date, time, datetime
from typing import Optional, List, Dict, Any
from loguru import logger

from src.data.database import db_manager
from src.data.models import SignalRecord, SignalType, ActionResult


class SignalRepository:
    """
    조건검색 시그널 Repository

    조건검색 편입/이탈 시그널 CRUD 및 분석 쿼리 제공
    """

    TABLE = "signals"

    def __init__(self, db=None):
        self.db = db or db_manager

    # ==================== 저장 ====================

    def save(self, signal: SignalRecord) -> int:
        """
        시그널 저장

        Args:
            signal: SignalRecord 객체

        Returns:
            저장된 시그널 ID
        """
        data = {
            "signal_date": signal.signal_date.isoformat() if isinstance(signal.signal_date, date) else signal.signal_date,
            "signal_time": signal.signal_time.isoformat() if isinstance(signal.signal_time, time) else signal.signal_time,
            "stock_code": signal.stock_code,
            "stock_name": signal.stock_name,
            "condition_name": signal.condition_name,
            "signal_type": signal.signal_type.value if isinstance(signal.signal_type, SignalType) else signal.signal_type,
            "current_price": signal.current_price,
            "volume": signal.volume,
            "change_rate": signal.change_rate,
            "acted": signal.acted,
            "action_result": signal.action_result.value if isinstance(signal.action_result, ActionResult) else signal.action_result,
            "skip_reason": signal.skip_reason,
        }

        signal_id = self.db.insert(self.TABLE, data)
        signal.id = signal_id

        logger.debug(f"Signal saved: {signal.signal_type.value} {signal.stock_code} ({signal.condition_name})")
        return signal_id

    def save_in_signal(
        self,
        stock_code: str,
        stock_name: str,
        condition_name: str,
        current_price: int = None,
        volume: int = None,
        change_rate: float = None
    ) -> SignalRecord:
        """
        편입 시그널 저장 (편의 메서드)

        Returns:
            저장된 SignalRecord
        """
        now = datetime.now()
        signal = SignalRecord(
            signal_date=now.date(),
            signal_time=now.time(),
            stock_code=stock_code,
            stock_name=stock_name,
            condition_name=condition_name,
            signal_type=SignalType.IN,
            current_price=current_price,
            volume=volume,
            change_rate=change_rate,
        )
        self.save(signal)
        return signal

    def save_out_signal(
        self,
        stock_code: str,
        stock_name: str,
        condition_name: str,
        current_price: int = None
    ) -> SignalRecord:
        """
        이탈 시그널 저장 (편의 메서드)

        Returns:
            저장된 SignalRecord
        """
        now = datetime.now()
        signal = SignalRecord(
            signal_date=now.date(),
            signal_time=now.time(),
            stock_code=stock_code,
            stock_name=stock_name,
            condition_name=condition_name,
            signal_type=SignalType.OUT,
            current_price=current_price,
        )
        self.save(signal)
        return signal

    def update_action(
        self,
        signal_id: int,
        acted: bool,
        action_result: ActionResult,
        skip_reason: str = None
    ):
        """
        시그널 액션 결과 업데이트

        매매 결정 후 해당 시그널에 결과 기록
        """
        data = {
            "acted": acted,
            "action_result": action_result.value if isinstance(action_result, ActionResult) else action_result,
            "skip_reason": skip_reason,
        }
        self.db.update(self.TABLE, data, "id = ?", (signal_id,))
        logger.debug(f"Signal {signal_id} action updated: {action_result}")

    # ==================== 조회 ====================

    def _row_to_record(self, row) -> SignalRecord:
        """DB Row → SignalRecord 변환"""
        return SignalRecord(
            id=row["id"],
            signal_date=date.fromisoformat(row["signal_date"]) if row["signal_date"] else None,
            signal_time=time.fromisoformat(row["signal_time"]) if row["signal_time"] else None,
            stock_code=row["stock_code"],
            stock_name=row["stock_name"],
            condition_name=row["condition_name"],
            signal_type=SignalType(row["signal_type"]),
            current_price=row["current_price"],
            volume=row["volume"],
            change_rate=row["change_rate"],
            acted=bool(row["acted"]),
            action_result=ActionResult(row["action_result"]) if row["action_result"] else None,
            skip_reason=row["skip_reason"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    def get_by_id(self, signal_id: int) -> Optional[SignalRecord]:
        """ID로 시그널 조회"""
        query = f"SELECT * FROM {self.TABLE} WHERE id = ?"
        row = self.db.fetchone(query, (signal_id,))
        return self._row_to_record(row) if row else None

    def get_by_date(self, signal_date: date) -> List[SignalRecord]:
        """일별 시그널 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE signal_date = ?
            ORDER BY signal_time ASC
        """
        rows = self.db.fetchall(query, (signal_date.isoformat(),))
        return [self._row_to_record(row) for row in rows]

    def get_by_period(
        self,
        start_date: date,
        end_date: date,
        signal_type: SignalType = None,
        condition_name: str = None
    ) -> List[SignalRecord]:
        """
        기간별 시그널 조회

        Args:
            start_date: 시작일
            end_date: 종료일
            signal_type: 시그널 타입 필터
            condition_name: 조건검색명 필터
        """
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE signal_date BETWEEN ? AND ?
        """
        params = [start_date.isoformat(), end_date.isoformat()]

        if signal_type:
            query += " AND signal_type = ?"
            params.append(signal_type.value)

        if condition_name:
            query += " AND condition_name = ?"
            params.append(condition_name)

        query += " ORDER BY signal_date ASC, signal_time ASC"

        rows = self.db.fetchall(query, tuple(params))
        return [self._row_to_record(row) for row in rows]

    def get_by_stock(
        self,
        stock_code: str,
        limit: int = 100
    ) -> List[SignalRecord]:
        """종목별 시그널 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE stock_code = ?
            ORDER BY signal_date DESC, signal_time DESC
            LIMIT ?
        """
        rows = self.db.fetchall(query, (stock_code, limit))
        return [self._row_to_record(row) for row in rows]

    def get_by_condition(
        self,
        condition_name: str,
        limit: int = 100
    ) -> List[SignalRecord]:
        """조건검색별 시그널 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE condition_name = ?
            ORDER BY signal_date DESC, signal_time DESC
            LIMIT ?
        """
        rows = self.db.fetchall(query, (condition_name, limit))
        return [self._row_to_record(row) for row in rows]

    def get_recent(self, limit: int = 50) -> List[SignalRecord]:
        """최근 시그널 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            ORDER BY signal_date DESC, signal_time DESC
            LIMIT ?
        """
        rows = self.db.fetchall(query, (limit,))
        return [self._row_to_record(row) for row in rows]

    def get_today_signals(self) -> List[SignalRecord]:
        """오늘 시그널 조회"""
        return self.get_by_date(date.today())

    def get_unacted_signals(self, signal_date: date = None) -> List[SignalRecord]:
        """
        미처리 시그널 조회 (편입 시그널 중 acted=False)
        """
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE signal_type = 'IN'
              AND acted = FALSE
        """
        params = []

        if signal_date:
            query += " AND signal_date = ?"
            params.append(signal_date.isoformat())

        query += " ORDER BY signal_date ASC, signal_time ASC"

        rows = self.db.fetchall(query, tuple(params) if params else None)
        return [self._row_to_record(row) for row in rows]

    # ==================== 통계 ====================

    def get_daily_stats(self, signal_date: date) -> Dict[str, Any]:
        """
        일별 시그널 통계

        Returns:
            {
                "total_count": 전체 시그널 수,
                "in_count": 편입 수,
                "out_count": 이탈 수,
                "acted_count": 실행된 시그널 수,
                "buy_count": 매수 실행 수,
                "skip_count": 스킵 수,
            }
        """
        query = """
            SELECT
                COUNT(*) as total_count,
                SUM(CASE WHEN signal_type = 'IN' THEN 1 ELSE 0 END) as in_count,
                SUM(CASE WHEN signal_type = 'OUT' THEN 1 ELSE 0 END) as out_count,
                SUM(CASE WHEN acted = TRUE THEN 1 ELSE 0 END) as acted_count,
                SUM(CASE WHEN action_result = 'BUY' THEN 1 ELSE 0 END) as buy_count,
                SUM(CASE WHEN action_result = 'SKIP' THEN 1 ELSE 0 END) as skip_count,
                SUM(CASE WHEN action_result = 'FILTERED' THEN 1 ELSE 0 END) as filtered_count
            FROM signals
            WHERE signal_date = ?
        """
        row = self.db.fetchone(query, (signal_date.isoformat(),))

        if not row:
            return {
                "total_count": 0, "in_count": 0, "out_count": 0,
                "acted_count": 0, "buy_count": 0, "skip_count": 0,
                "filtered_count": 0,
            }

        return dict(row)

    def get_condition_stats(self, condition_name: str) -> Dict[str, Any]:
        """
        조건검색별 시그널 통계

        Returns:
            {
                "total_count": 전체 시그널 수,
                "in_count": 편입 수,
                "acted_count": 실행된 시그널 수,
                "buy_rate": 매수 실행률 (%),
            }
        """
        query = """
            SELECT
                COUNT(*) as total_count,
                SUM(CASE WHEN signal_type = 'IN' THEN 1 ELSE 0 END) as in_count,
                SUM(CASE WHEN acted = TRUE THEN 1 ELSE 0 END) as acted_count,
                SUM(CASE WHEN action_result = 'BUY' THEN 1 ELSE 0 END) as buy_count
            FROM signals
            WHERE condition_name = ?
        """
        row = self.db.fetchone(query, (condition_name,))

        if not row:
            return {"total_count": 0, "in_count": 0, "acted_count": 0, "buy_rate": 0.0}

        result = dict(row)
        result["buy_rate"] = (result["buy_count"] / result["in_count"] * 100) if result["in_count"] > 0 else 0.0
        return result

    def get_skip_reasons_summary(self, signal_date: date = None) -> Dict[str, int]:
        """
        스킵 사유별 집계

        Returns:
            {"중복 보유": 5, "거래량 부족": 3, ...}
        """
        query = """
            SELECT skip_reason, COUNT(*) as count
            FROM signals
            WHERE action_result IN ('SKIP', 'FILTERED')
              AND skip_reason IS NOT NULL
        """
        params = []

        if signal_date:
            query += " AND signal_date = ?"
            params.append(signal_date.isoformat())

        query += " GROUP BY skip_reason ORDER BY count DESC"

        rows = self.db.fetchall(query, tuple(params) if params else None)
        return {row["skip_reason"]: row["count"] for row in rows}

    def get_hourly_distribution(self, signal_date: date) -> Dict[int, int]:
        """
        시간대별 시그널 분포

        Returns:
            {9: 15, 10: 23, 11: 8, ...}  # 시간: 시그널 수
        """
        query = """
            SELECT
                CAST(SUBSTR(signal_time, 1, 2) AS INTEGER) as hour,
                COUNT(*) as count
            FROM signals
            WHERE signal_date = ?
              AND signal_type = 'IN'
            GROUP BY hour
            ORDER BY hour
        """
        rows = self.db.fetchall(query, (signal_date.isoformat(),))
        return {row["hour"]: row["count"] for row in rows}

    # ==================== 삭제 ====================

    def delete(self, signal_id: int) -> bool:
        """시그널 삭제"""
        count = self.db.delete(self.TABLE, "id = ?", (signal_id,))
        return count > 0

    def delete_by_date(self, signal_date: date) -> int:
        """일별 시그널 삭제"""
        return self.db.delete(self.TABLE, "signal_date = ?", (signal_date.isoformat(),))


# 전역 인스턴스
signal_repository = SignalRepository()
