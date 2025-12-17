"""
K-Hunter Trading System - Daily Summary Repository
일별 거래 요약 저장/조회
"""

from datetime import date, datetime
from typing import Optional, List, Dict, Any
from loguru import logger

from src.data.database import db_manager
from src.data.models import DailySummary
from src.data.repositories.trade_repository import trade_repository
from src.data.repositories.signal_repository import signal_repository


class DailySummaryRepository:
    """
    일별 거래 요약 Repository

    일별 통계 데이터 관리 및 자동 집계 기능 제공
    """

    TABLE = "daily_summary"

    def __init__(self, db=None):
        self.db = db or db_manager

    # ==================== 저장 ====================

    def save(self, summary: DailySummary) -> int:
        """
        일별 요약 저장

        Args:
            summary: DailySummary 객체

        Returns:
            저장된 ID
        """
        data = {
            "trade_date": summary.trade_date.isoformat() if isinstance(summary.trade_date, date) else summary.trade_date,
            "starting_balance": summary.starting_balance,
            "ending_balance": summary.ending_balance,
            "total_profit": summary.total_profit,
            "profit_rate": summary.profit_rate,
            "trade_count": summary.trade_count,
            "buy_count": summary.buy_count,
            "sell_count": summary.sell_count,
            "win_count": summary.win_count,
            "loss_count": summary.loss_count,
            "even_count": summary.even_count,
            "win_rate": summary.win_rate,
            "max_profit": summary.max_profit,
            "max_loss": summary.max_loss,
            "avg_profit": summary.avg_profit,
            "avg_loss": summary.avg_loss,
            "signal_count": summary.signal_count,
            "signal_acted_count": summary.signal_acted_count,
        }

        summary_id = self.db.insert(self.TABLE, data)
        summary.id = summary_id

        logger.debug(f"Daily summary saved: {summary.trade_date} (profit: {summary.total_profit:,})")
        return summary_id

    def upsert(self, summary: DailySummary) -> int:
        """
        일별 요약 저장/갱신 (Upsert)

        해당 날짜의 요약이 이미 존재하면 업데이트, 없으면 삽입
        """
        existing = self.get_by_date(summary.trade_date)

        if existing:
            # 업데이트
            data = {
                "starting_balance": summary.starting_balance,
                "ending_balance": summary.ending_balance,
                "total_profit": summary.total_profit,
                "profit_rate": summary.profit_rate,
                "trade_count": summary.trade_count,
                "buy_count": summary.buy_count,
                "sell_count": summary.sell_count,
                "win_count": summary.win_count,
                "loss_count": summary.loss_count,
                "even_count": summary.even_count,
                "win_rate": summary.win_rate,
                "max_profit": summary.max_profit,
                "max_loss": summary.max_loss,
                "avg_profit": summary.avg_profit,
                "avg_loss": summary.avg_loss,
                "signal_count": summary.signal_count,
                "signal_acted_count": summary.signal_acted_count,
                "updated_at": datetime.now().isoformat(),
            }
            self.db.update(self.TABLE, data, "id = ?", (existing.id,))
            summary.id = existing.id
            logger.debug(f"Daily summary updated: {summary.trade_date}")
            return existing.id
        else:
            # 삽입
            return self.save(summary)

    def calculate_and_save(
        self,
        trade_date: date,
        starting_balance: int = 0,
        ending_balance: int = 0
    ) -> DailySummary:
        """
        일별 통계 자동 계산 및 저장

        거래 및 시그널 데이터를 기반으로 자동 집계

        Args:
            trade_date: 대상 날짜
            starting_balance: 시작 잔고
            ending_balance: 종료 잔고

        Returns:
            계산된 DailySummary
        """
        # 거래 통계 조회
        trade_stats = trade_repository.get_daily_stats(trade_date)

        # 시그널 통계 조회
        signal_stats = signal_repository.get_daily_stats(trade_date)

        # 수익 거래 평균
        avg_profit = 0.0
        avg_loss = 0.0
        if trade_stats["win_count"] > 0:
            # 수익 거래만 조회해서 평균 계산
            query = """
                SELECT AVG(profit) as avg_profit
                FROM trades
                WHERE trade_date = ? AND profit > 0
            """
            row = self.db.fetchone(query, (trade_date.isoformat(),))
            avg_profit = row["avg_profit"] if row and row["avg_profit"] else 0.0

        if trade_stats["loss_count"] > 0:
            query = """
                SELECT AVG(profit) as avg_loss
                FROM trades
                WHERE trade_date = ? AND profit < 0
            """
            row = self.db.fetchone(query, (trade_date.isoformat(),))
            avg_loss = row["avg_loss"] if row and row["avg_loss"] else 0.0

        # 승률 계산
        sell_count = trade_stats["sell_count"] or 0
        win_rate = 0.0
        if sell_count > 0:
            win_rate = (trade_stats["win_count"] / sell_count) * 100

        # 수익률 계산
        profit_rate = 0.0
        if starting_balance > 0:
            profit_rate = (trade_stats["total_profit"] / starting_balance) * 100

        summary = DailySummary(
            trade_date=trade_date,
            starting_balance=starting_balance,
            ending_balance=ending_balance,
            total_profit=trade_stats["total_profit"] or 0,
            profit_rate=profit_rate,
            trade_count=trade_stats["trade_count"] or 0,
            buy_count=trade_stats["buy_count"] or 0,
            sell_count=trade_stats["sell_count"] or 0,
            win_count=trade_stats["win_count"] or 0,
            loss_count=trade_stats["loss_count"] or 0,
            even_count=trade_stats.get("even_count", 0) or 0,
            win_rate=win_rate,
            max_profit=trade_stats["max_profit"] or 0,
            max_loss=trade_stats["max_loss"] or 0,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            signal_count=signal_stats["total_count"] or 0,
            signal_acted_count=signal_stats["acted_count"] or 0,
        )

        self.upsert(summary)
        return summary

    # ==================== 조회 ====================

    def _row_to_record(self, row) -> DailySummary:
        """DB Row → DailySummary 변환"""
        # trade_date 처리 (이미 date 객체일 수 있음)
        trade_date_val = row["trade_date"]
        if trade_date_val:
            if isinstance(trade_date_val, date):
                trade_date = trade_date_val
            else:
                trade_date = date.fromisoformat(trade_date_val)
        else:
            trade_date = None

        # datetime 필드 처리
        created_at_val = row["created_at"]
        if created_at_val:
            if isinstance(created_at_val, datetime):
                created_at = created_at_val
            else:
                created_at = datetime.fromisoformat(created_at_val)
        else:
            created_at = None

        updated_at_val = row["updated_at"]
        if updated_at_val:
            if isinstance(updated_at_val, datetime):
                updated_at = updated_at_val
            else:
                updated_at = datetime.fromisoformat(updated_at_val)
        else:
            updated_at = None

        return DailySummary(
            id=row["id"],
            trade_date=trade_date,
            starting_balance=row["starting_balance"] or 0,
            ending_balance=row["ending_balance"] or 0,
            total_profit=row["total_profit"] or 0,
            profit_rate=row["profit_rate"] or 0.0,
            trade_count=row["trade_count"] or 0,
            buy_count=row["buy_count"] or 0,
            sell_count=row["sell_count"] or 0,
            win_count=row["win_count"] or 0,
            loss_count=row["loss_count"] or 0,
            even_count=row["even_count"] or 0,
            win_rate=row["win_rate"] or 0.0,
            max_profit=row["max_profit"] or 0,
            max_loss=row["max_loss"] or 0,
            avg_profit=row["avg_profit"] or 0.0,
            avg_loss=row["avg_loss"] or 0.0,
            signal_count=row["signal_count"] or 0,
            signal_acted_count=row["signal_acted_count"] or 0,
            created_at=created_at,
            updated_at=updated_at,
        )

    def get_by_date(self, trade_date: date) -> Optional[DailySummary]:
        """일자별 요약 조회"""
        query = f"SELECT * FROM {self.TABLE} WHERE trade_date = ?"
        row = self.db.fetchone(query, (trade_date.isoformat(),))
        return self._row_to_record(row) if row else None

    def get_by_period(
        self,
        start_date: date,
        end_date: date
    ) -> List[DailySummary]:
        """기간별 요약 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date ASC
        """
        rows = self.db.fetchall(query, (start_date.isoformat(), end_date.isoformat()))
        return [self._row_to_record(row) for row in rows]

    def get_recent(self, days: int = 30) -> List[DailySummary]:
        """최근 N일 요약 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            ORDER BY trade_date DESC
            LIMIT ?
        """
        rows = self.db.fetchall(query, (days,))
        return [self._row_to_record(row) for row in rows]

    def get_all(self) -> List[DailySummary]:
        """전체 요약 조회"""
        query = f"SELECT * FROM {self.TABLE} ORDER BY trade_date ASC"
        rows = self.db.fetchall(query)
        return [self._row_to_record(row) for row in rows]

    # ==================== 통계 ====================

    def get_period_stats(self, start_date: date, end_date: date) -> Dict[str, Any]:
        """
        기간별 종합 통계

        Returns:
            {
                "days": 거래일 수,
                "total_profit": 총 수익,
                "avg_daily_profit": 일평균 수익,
                "total_trades": 총 거래 수,
                "win_rate": 승률,
                "max_profit": 최대 일 수익,
                "max_loss": 최대 일 손실,
                "profitable_days": 수익일 수,
                "loss_days": 손실일 수,
            }
        """
        query = """
            SELECT
                COUNT(*) as days,
                COALESCE(SUM(total_profit), 0) as total_profit,
                COALESCE(AVG(total_profit), 0) as avg_daily_profit,
                COALESCE(SUM(trade_count), 0) as total_trades,
                COALESCE(SUM(win_count), 0) as total_wins,
                COALESCE(SUM(loss_count), 0) as total_losses,
                COALESCE(MAX(total_profit), 0) as max_profit,
                COALESCE(MIN(total_profit), 0) as max_loss,
                SUM(CASE WHEN total_profit > 0 THEN 1 ELSE 0 END) as profitable_days,
                SUM(CASE WHEN total_profit < 0 THEN 1 ELSE 0 END) as loss_days
            FROM daily_summary
            WHERE trade_date BETWEEN ? AND ?
        """
        row = self.db.fetchone(query, (start_date.isoformat(), end_date.isoformat()))

        if not row:
            return {
                "days": 0, "total_profit": 0, "avg_daily_profit": 0,
                "total_trades": 0, "win_rate": 0.0,
                "max_profit": 0, "max_loss": 0,
                "profitable_days": 0, "loss_days": 0,
            }

        result = dict(row)

        # 승률 계산
        total_completed = result["total_wins"] + result["total_losses"]
        result["win_rate"] = (result["total_wins"] / total_completed * 100) if total_completed > 0 else 0.0

        return result

    def get_cumulative_profit(self, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """
        누적 수익 조회 (차트용)

        Returns:
            [{"date": "2024-01-01", "profit": 100000, "cumulative": 100000}, ...]
        """
        query = """
            SELECT
                trade_date,
                total_profit,
                SUM(total_profit) OVER (ORDER BY trade_date) as cumulative
            FROM daily_summary
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """
        rows = self.db.fetchall(query, (start_date.isoformat(), end_date.isoformat()))
        return [
            {
                "date": row["trade_date"],
                "profit": row["total_profit"],
                "cumulative": row["cumulative"],
            }
            for row in rows
        ]

    def get_monthly_summary(self, year: int, month: int = None) -> List[Dict[str, Any]]:
        """
        월별 요약 (연간 분석용)

        Args:
            year: 연도
            month: 월 (None이면 전체 월)

        Returns:
            월별 통계 리스트
        """
        if month:
            where_clause = "strftime('%Y-%m', trade_date) = ?"
            params = (f"{year:04d}-{month:02d}",)
        else:
            where_clause = "strftime('%Y', trade_date) = ?"
            params = (str(year),)

        query = f"""
            SELECT
                strftime('%Y-%m', trade_date) as month,
                COUNT(*) as days,
                SUM(total_profit) as total_profit,
                AVG(total_profit) as avg_daily_profit,
                SUM(trade_count) as total_trades,
                SUM(win_count) as win_count,
                SUM(loss_count) as loss_count
            FROM daily_summary
            WHERE {where_clause}
            GROUP BY month
            ORDER BY month
        """
        rows = self.db.fetchall(query, params)
        return [dict(row) for row in rows]

    def calculate_mdd(self, start_date: date, end_date: date) -> Dict[str, Any]:
        """
        MDD (Maximum Drawdown) 계산

        Returns:
            {
                "mdd": MDD 값 (음수),
                "mdd_rate": MDD 비율 (%),
                "peak_date": 고점 날짜,
                "trough_date": 저점 날짜,
            }
        """
        # 누적 수익 조회
        cumulative = self.get_cumulative_profit(start_date, end_date)

        if not cumulative:
            return {"mdd": 0, "mdd_rate": 0.0, "peak_date": None, "trough_date": None}

        peak = 0
        peak_date = None
        mdd = 0
        mdd_rate = 0.0
        trough_date = None

        for item in cumulative:
            cum = item["cumulative"]
            if cum > peak:
                peak = cum
                peak_date = item["date"]

            drawdown = cum - peak
            if drawdown < mdd:
                mdd = drawdown
                trough_date = item["date"]
                mdd_rate = (mdd / peak * 100) if peak > 0 else 0.0

        return {
            "mdd": mdd,
            "mdd_rate": mdd_rate,
            "peak_date": peak_date,
            "trough_date": trough_date,
        }

    # ==================== 삭제 ====================

    def delete(self, summary_id: int) -> bool:
        """요약 삭제"""
        count = self.db.delete(self.TABLE, "id = ?", (summary_id,))
        return count > 0

    def delete_by_date(self, trade_date: date) -> int:
        """일자별 요약 삭제"""
        return self.db.delete(self.TABLE, "trade_date = ?", (trade_date.isoformat(),))


# 전역 인스턴스
daily_repository = DailySummaryRepository()
