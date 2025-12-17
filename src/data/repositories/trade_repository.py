"""
K-Hunter Trading System - Trade Repository
거래 이력 저장/조회
"""

from datetime import date, time, datetime
from typing import Optional, List, Dict, Any
from loguru import logger

from src.data.database import db_manager
from src.data.models import TradeRecord, TradeSide


class TradeRepository:
    """
    거래 이력 Repository

    거래(체결) 데이터 CRUD 및 분석 쿼리 제공
    """

    TABLE = "trades"

    def __init__(self, db=None):
        self.db = db or db_manager

    # ==================== 저장 ====================

    def save(self, trade: TradeRecord) -> int:
        """
        거래 저장

        Args:
            trade: TradeRecord 객체

        Returns:
            저장된 거래 ID
        """
        data = {
            "trade_date": trade.trade_date.isoformat() if isinstance(trade.trade_date, date) else trade.trade_date,
            "trade_time": trade.trade_time.isoformat() if isinstance(trade.trade_time, time) else trade.trade_time,
            "stock_code": trade.stock_code,
            "stock_name": trade.stock_name,
            "side": trade.side.value if isinstance(trade.side, TradeSide) else trade.side,
            "quantity": trade.quantity,
            "price": trade.price,
            "amount": trade.amount,
            "fee": trade.fee,
            "tax": trade.tax,
            "profit": trade.profit,
            "profit_rate": trade.profit_rate,
            "condition_name": trade.condition_name,
            "strategy": trade.strategy,
            "memo": trade.memo,
            "buy_trade_id": trade.buy_trade_id,
        }

        trade_id = self.db.insert(self.TABLE, data)
        trade.id = trade_id

        logger.debug(f"Trade saved: {trade.side.value} {trade.stock_code} x{trade.quantity} @ {trade.price:,}")
        return trade_id

    def save_buy(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        price: int,
        condition_name: str = None,
        strategy: str = None,
        fee: int = 0
    ) -> TradeRecord:
        """
        매수 거래 저장 (편의 메서드)

        Returns:
            저장된 TradeRecord
        """
        now = datetime.now()
        trade = TradeRecord(
            trade_date=now.date(),
            trade_time=now.time(),
            stock_code=stock_code,
            stock_name=stock_name,
            side=TradeSide.BUY,
            quantity=quantity,
            price=price,
            amount=price * quantity,
            fee=fee,
            condition_name=condition_name,
            strategy=strategy,
        )
        self.save(trade)
        return trade

    def save_sell(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        price: int,
        buy_price: int = None,
        buy_trade_id: int = None,
        condition_name: str = None,
        strategy: str = None,
        fee: int = 0,
        tax: int = 0
    ) -> TradeRecord:
        """
        매도 거래 저장 (편의 메서드)

        Args:
            buy_price: 매수 가격 (수익률 계산용)
            buy_trade_id: 매수 거래 ID (매칭용)

        Returns:
            저장된 TradeRecord
        """
        now = datetime.now()
        amount = price * quantity

        # 수익 계산
        profit = None
        profit_rate = None
        if buy_price and buy_price > 0:
            cost = buy_price * quantity
            profit = amount - cost - fee - tax
            profit_rate = (profit / cost) * 100

        trade = TradeRecord(
            trade_date=now.date(),
            trade_time=now.time(),
            stock_code=stock_code,
            stock_name=stock_name,
            side=TradeSide.SELL,
            quantity=quantity,
            price=price,
            amount=amount,
            fee=fee,
            tax=tax,
            profit=profit,
            profit_rate=profit_rate,
            condition_name=condition_name,
            strategy=strategy,
            buy_trade_id=buy_trade_id,
        )
        self.save(trade)
        return trade

    # ==================== 조회 ====================

    def _row_to_record(self, row) -> TradeRecord:
        """DB Row → TradeRecord 변환"""
        return TradeRecord(
            id=row["id"],
            trade_date=date.fromisoformat(row["trade_date"]) if row["trade_date"] else None,
            trade_time=time.fromisoformat(row["trade_time"]) if row["trade_time"] else None,
            stock_code=row["stock_code"],
            stock_name=row["stock_name"],
            side=TradeSide(row["side"]),
            quantity=row["quantity"],
            price=row["price"],
            amount=row["amount"],
            fee=row["fee"] or 0,
            tax=row["tax"] or 0,
            profit=row["profit"],
            profit_rate=row["profit_rate"],
            condition_name=row["condition_name"],
            strategy=row["strategy"],
            memo=row["memo"],
            buy_trade_id=row["buy_trade_id"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    def get_by_id(self, trade_id: int) -> Optional[TradeRecord]:
        """ID로 거래 조회"""
        query = f"SELECT * FROM {self.TABLE} WHERE id = ?"
        row = self.db.fetchone(query, (trade_id,))
        return self._row_to_record(row) if row else None

    def get_by_date(self, trade_date: date) -> List[TradeRecord]:
        """일별 거래 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE trade_date = ?
            ORDER BY trade_time ASC
        """
        rows = self.db.fetchall(query, (trade_date.isoformat(),))
        return [self._row_to_record(row) for row in rows]

    def get_by_period(
        self,
        start_date: date,
        end_date: date,
        side: TradeSide = None
    ) -> List[TradeRecord]:
        """
        기간별 거래 조회

        Args:
            start_date: 시작일
            end_date: 종료일
            side: 거래 방향 필터 (None=전체)
        """
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE trade_date BETWEEN ? AND ?
        """
        params = [start_date.isoformat(), end_date.isoformat()]

        if side:
            query += " AND side = ?"
            params.append(side.value)

        query += " ORDER BY trade_date ASC, trade_time ASC"

        rows = self.db.fetchall(query, tuple(params))
        return [self._row_to_record(row) for row in rows]

    def get_by_stock(
        self,
        stock_code: str,
        limit: int = 100
    ) -> List[TradeRecord]:
        """종목별 거래 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE stock_code = ?
            ORDER BY trade_date DESC, trade_time DESC
            LIMIT ?
        """
        rows = self.db.fetchall(query, (stock_code, limit))
        return [self._row_to_record(row) for row in rows]

    def get_by_condition(
        self,
        condition_name: str,
        limit: int = 100
    ) -> List[TradeRecord]:
        """조건검색별 거래 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE condition_name = ?
            ORDER BY trade_date DESC, trade_time DESC
            LIMIT ?
        """
        rows = self.db.fetchall(query, (condition_name, limit))
        return [self._row_to_record(row) for row in rows]

    def get_recent(self, limit: int = 50) -> List[TradeRecord]:
        """최근 거래 조회"""
        query = f"""
            SELECT * FROM {self.TABLE}
            ORDER BY trade_date DESC, trade_time DESC
            LIMIT ?
        """
        rows = self.db.fetchall(query, (limit,))
        return [self._row_to_record(row) for row in rows]

    def get_today_trades(self) -> List[TradeRecord]:
        """오늘 거래 조회"""
        return self.get_by_date(date.today())

    # ==================== 통계 ====================

    def get_daily_stats(self, trade_date: date) -> Dict[str, Any]:
        """
        일별 거래 통계

        Returns:
            {
                "trade_count": 전체 거래 수,
                "buy_count": 매수 수,
                "sell_count": 매도 수,
                "total_profit": 총 실현손익,
                "win_count": 수익 거래 수,
                "loss_count": 손실 거래 수,
                "max_profit": 최대 수익,
                "max_loss": 최대 손실,
            }
        """
        query = """
            SELECT
                COUNT(*) as trade_count,
                SUM(CASE WHEN side = 'BUY' THEN 1 ELSE 0 END) as buy_count,
                SUM(CASE WHEN side = 'SELL' THEN 1 ELSE 0 END) as sell_count,
                COALESCE(SUM(CASE WHEN side = 'SELL' THEN profit ELSE 0 END), 0) as total_profit,
                SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as loss_count,
                SUM(CASE WHEN profit = 0 AND side = 'SELL' THEN 1 ELSE 0 END) as even_count,
                COALESCE(MAX(CASE WHEN profit > 0 THEN profit END), 0) as max_profit,
                COALESCE(MIN(CASE WHEN profit < 0 THEN profit END), 0) as max_loss
            FROM trades
            WHERE trade_date = ?
        """
        row = self.db.fetchone(query, (trade_date.isoformat(),))

        if not row:
            return {
                "trade_count": 0, "buy_count": 0, "sell_count": 0,
                "total_profit": 0, "win_count": 0, "loss_count": 0,
                "even_count": 0, "max_profit": 0, "max_loss": 0,
            }

        return dict(row)

    def get_stock_stats(self, stock_code: str) -> Dict[str, Any]:
        """종목별 거래 통계"""
        query = """
            SELECT
                COUNT(*) as trade_count,
                SUM(CASE WHEN side = 'SELL' THEN profit ELSE 0 END) as total_profit,
                AVG(CASE WHEN profit IS NOT NULL THEN profit END) as avg_profit,
                SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as loss_count
            FROM trades
            WHERE stock_code = ?
        """
        row = self.db.fetchone(query, (stock_code,))
        return dict(row) if row else {}

    def get_condition_stats(self, condition_name: str) -> Dict[str, Any]:
        """조건검색별 거래 통계"""
        query = """
            SELECT
                COUNT(*) as trade_count,
                SUM(CASE WHEN side = 'SELL' THEN profit ELSE 0 END) as total_profit,
                AVG(CASE WHEN profit IS NOT NULL THEN profit END) as avg_profit,
                SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as loss_count
            FROM trades
            WHERE condition_name = ?
        """
        row = self.db.fetchone(query, (condition_name,))
        return dict(row) if row else {}

    def get_period_profit(self, start_date: date, end_date: date) -> int:
        """기간 총 수익"""
        query = """
            SELECT COALESCE(SUM(profit), 0) as total_profit
            FROM trades
            WHERE trade_date BETWEEN ? AND ?
              AND side = 'SELL'
              AND profit IS NOT NULL
        """
        row = self.db.fetchone(query, (start_date.isoformat(), end_date.isoformat()))
        return row["total_profit"] if row else 0

    # ==================== 매칭 ====================

    def find_unmatched_buy(self, stock_code: str) -> Optional[TradeRecord]:
        """
        매칭되지 않은 매수 거래 찾기 (FIFO)

        매도 시 해당 매수 거래를 찾아 연결하기 위함
        """
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE stock_code = ?
              AND side = 'BUY'
              AND id NOT IN (
                  SELECT buy_trade_id FROM {self.TABLE}
                  WHERE buy_trade_id IS NOT NULL
              )
            ORDER BY trade_date ASC, trade_time ASC
            LIMIT 1
        """
        row = self.db.fetchone(query, (stock_code,))
        return self._row_to_record(row) if row else None

    # ==================== 삭제 ====================

    def delete(self, trade_id: int) -> bool:
        """거래 삭제"""
        count = self.db.delete(self.TABLE, "id = ?", (trade_id,))
        return count > 0

    def delete_by_date(self, trade_date: date) -> int:
        """일별 거래 삭제"""
        return self.db.delete(self.TABLE, "trade_date = ?", (trade_date.isoformat(),))


# 전역 인스턴스
trade_repository = TradeRepository()
