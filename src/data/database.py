"""
K-Hunter Trading System - Database Manager
SQLite 데이터베이스 관리
"""

import sqlite3
import os
import threading
from datetime import datetime
from typing import Optional, List, Any, Dict
from contextlib import contextmanager
from pathlib import Path

from loguru import logger


class DatabaseManager:
    """
    SQLite 데이터베이스 관리자

    Features:
    - 테이블 자동 생성/마이그레이션
    - 스레드 안전한 연결 관리
    - 트랜잭션 지원
    - 백업 기능
    """

    # 스키마 버전 (마이그레이션 관리용)
    SCHEMA_VERSION = 1

    # 테이블 생성 SQL
    CREATE_TABLES_SQL = """
    -- 스키마 버전 관리
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 거래 이력
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE NOT NULL,
        trade_time TIME NOT NULL,
        stock_code VARCHAR(10) NOT NULL,
        stock_name VARCHAR(50),
        side VARCHAR(4) NOT NULL,
        quantity INTEGER NOT NULL,
        price INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        fee INTEGER DEFAULT 0,
        tax INTEGER DEFAULT 0,
        profit INTEGER,
        profit_rate REAL,
        condition_name VARCHAR(100),
        strategy VARCHAR(50),
        memo TEXT,
        buy_trade_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (buy_trade_id) REFERENCES trades(id)
    );

    -- 조건검색 시그널 이력
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_date DATE NOT NULL,
        signal_time TIME NOT NULL,
        stock_code VARCHAR(10) NOT NULL,
        stock_name VARCHAR(50),
        condition_name VARCHAR(100) NOT NULL,
        signal_type VARCHAR(10) NOT NULL,
        current_price INTEGER,
        volume INTEGER,
        change_rate REAL,
        acted BOOLEAN DEFAULT FALSE,
        action_result VARCHAR(20),
        skip_reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 일별 요약
    CREATE TABLE IF NOT EXISTS daily_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE UNIQUE NOT NULL,
        starting_balance INTEGER DEFAULT 0,
        ending_balance INTEGER DEFAULT 0,
        total_profit INTEGER DEFAULT 0,
        profit_rate REAL DEFAULT 0.0,
        trade_count INTEGER DEFAULT 0,
        buy_count INTEGER DEFAULT 0,
        sell_count INTEGER DEFAULT 0,
        win_count INTEGER DEFAULT 0,
        loss_count INTEGER DEFAULT 0,
        even_count INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        max_profit INTEGER DEFAULT 0,
        max_loss INTEGER DEFAULT 0,
        avg_profit REAL DEFAULT 0.0,
        avg_loss REAL DEFAULT 0.0,
        signal_count INTEGER DEFAULT 0,
        signal_acted_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP
    );

    -- 포지션 스냅샷
    CREATE TABLE IF NOT EXISTS position_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_time TIMESTAMP NOT NULL,
        stock_code VARCHAR(10) NOT NULL,
        stock_name VARCHAR(50),
        quantity INTEGER NOT NULL,
        avg_price INTEGER NOT NULL,
        current_price INTEGER DEFAULT 0,
        eval_amount INTEGER DEFAULT 0,
        profit INTEGER DEFAULT 0,
        profit_rate REAL DEFAULT 0.0
    );

    -- 인덱스 생성
    CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
    CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code);
    CREATE INDEX IF NOT EXISTS idx_trades_side ON trades(side);
    CREATE INDEX IF NOT EXISTS idx_trades_condition ON trades(condition_name);

    CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);
    CREATE INDEX IF NOT EXISTS idx_signals_stock ON signals(stock_code);
    CREATE INDEX IF NOT EXISTS idx_signals_condition ON signals(condition_name);
    CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type);

    CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_summary(trade_date);

    CREATE INDEX IF NOT EXISTS idx_snapshots_time ON position_snapshots(snapshot_time);
    CREATE INDEX IF NOT EXISTS idx_snapshots_stock ON position_snapshots(stock_code);
    """

    def __init__(self, db_path: str = "data/trading.db"):
        """
        DatabaseManager 초기화

        Args:
            db_path: 데이터베이스 파일 경로
        """
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()

        # 디렉토리 생성
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)

        # 초기화
        self._init_database()

        logger.info(f"DatabaseManager initialized: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """
        스레드별 연결 획득

        각 스레드마다 별도의 연결을 유지하여 스레드 안전성 보장
        """
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
                check_same_thread=False
            )
            # Row를 딕셔너리처럼 사용
            self._local.connection.row_factory = sqlite3.Row
            # 외래키 활성화
            self._local.connection.execute("PRAGMA foreign_keys = ON")

        return self._local.connection

    @contextmanager
    def get_connection(self):
        """컨텍스트 매니저로 연결 제공"""
        conn = self._get_connection()
        try:
            yield conn
        finally:
            pass  # 연결은 스레드가 살아있는 동안 유지

    @contextmanager
    def transaction(self):
        """
        트랜잭션 컨텍스트 매니저

        with db_manager.transaction() as conn:
            conn.execute("INSERT ...")
            conn.execute("UPDATE ...")
        # 자동 커밋 또는 롤백
        """
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction rollback: {e}")
            raise

    def _init_database(self):
        """데이터베이스 초기화 (테이블 생성)"""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 테이블 생성
            cursor.executescript(self.CREATE_TABLES_SQL)

            # 스키마 버전 확인/업데이트
            cursor.execute("SELECT MAX(version) FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row and row[0] else 0

            if current_version < self.SCHEMA_VERSION:
                # 마이그레이션 실행 (필요 시)
                self._run_migrations(cursor, current_version)
                cursor.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (self.SCHEMA_VERSION,)
                )

            conn.commit()
            logger.debug(f"Database initialized, schema version: {self.SCHEMA_VERSION}")

    def _run_migrations(self, cursor: sqlite3.Cursor, from_version: int):
        """
        스키마 마이그레이션 실행

        버전별 마이그레이션 SQL을 여기에 추가
        """
        # 예: if from_version < 2:
        #     cursor.execute("ALTER TABLE trades ADD COLUMN new_field ...")
        pass

    def execute(
        self,
        query: str,
        params: tuple = None,
        fetch: bool = False
    ) -> Optional[List[sqlite3.Row]]:
        """
        SQL 쿼리 실행

        Args:
            query: SQL 쿼리
            params: 파라미터 튜플
            fetch: 결과 반환 여부

        Returns:
            fetch=True인 경우 결과 리스트
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            if fetch:
                return cursor.fetchall()
            else:
                conn.commit()
                return None

        except Exception as e:
            conn.rollback()
            logger.error(f"Database execute error: {e}\nQuery: {query}")
            raise

    def execute_many(self, query: str, params_list: List[tuple]):
        """
        다중 행 삽입

        Args:
            query: SQL 쿼리
            params_list: 파라미터 튜플 리스트
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.executemany(query, params_list)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database execute_many error: {e}")
            raise

    def fetchone(self, query: str, params: tuple = None) -> Optional[sqlite3.Row]:
        """단일 행 조회"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        return cursor.fetchone()

    def fetchall(self, query: str, params: tuple = None) -> List[sqlite3.Row]:
        """모든 행 조회"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        return cursor.fetchall()

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        """
        데이터 삽입 후 ID 반환

        Args:
            table: 테이블명
            data: 삽입할 데이터 딕셔너리

        Returns:
            삽입된 행의 ID
        """
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(query, tuple(data.values()))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            conn.rollback()
            logger.error(f"Insert error: {e}")
            raise

    def update(
        self,
        table: str,
        data: Dict[str, Any],
        where: str,
        where_params: tuple
    ) -> int:
        """
        데이터 업데이트

        Args:
            table: 테이블명
            data: 업데이트할 데이터
            where: WHERE 절
            where_params: WHERE 파라미터

        Returns:
            업데이트된 행 수
        """
        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where}"
        params = tuple(data.values()) + where_params

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            logger.error(f"Update error: {e}")
            raise

    def delete(self, table: str, where: str, where_params: tuple) -> int:
        """
        데이터 삭제

        Returns:
            삭제된 행 수
        """
        query = f"DELETE FROM {table} WHERE {where}"

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(query, where_params)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            logger.error(f"Delete error: {e}")
            raise

    def count(self, table: str, where: str = None, where_params: tuple = None) -> int:
        """테이블 행 수 조회"""
        query = f"SELECT COUNT(*) FROM {table}"
        if where:
            query += f" WHERE {where}"

        row = self.fetchone(query, where_params)
        return row[0] if row else 0

    def backup(self, backup_path: str = None) -> str:
        """
        데이터베이스 백업

        Args:
            backup_path: 백업 파일 경로 (기본값: 자동 생성)

        Returns:
            백업 파일 경로
        """
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = os.path.dirname(self.db_path)
            backup_path = os.path.join(backup_dir, f"trading_backup_{timestamp}.db")

        with self._lock:
            conn = self._get_connection()
            backup_conn = sqlite3.connect(backup_path)

            try:
                conn.backup(backup_conn)
                logger.info(f"Database backed up to: {backup_path}")
                return backup_path
            finally:
                backup_conn.close()

    def vacuum(self):
        """데이터베이스 최적화 (VACUUM)"""
        conn = self._get_connection()
        conn.execute("VACUUM")
        logger.info("Database vacuumed")

    def get_table_stats(self) -> Dict[str, int]:
        """테이블별 행 수 통계"""
        tables = ["trades", "signals", "daily_summary", "position_snapshots"]
        stats = {}
        for table in tables:
            stats[table] = self.count(table)
        return stats

    def close(self):
        """연결 종료"""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
            logger.debug("Database connection closed")


# 전역 DatabaseManager 인스턴스
db_manager = DatabaseManager()
