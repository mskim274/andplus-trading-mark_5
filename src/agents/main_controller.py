"""
K-Hunter Trading System - Main Controller
메인 컨트롤러: 전체 시스템 오케스트레이션
"""

import sys
import yaml
from datetime import datetime, time as dt_time
from typing import Optional, Dict, Any
from pathlib import Path
from loguru import logger

from PyQt5.QtCore import QTimer, QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

from src.core.events import Event, EventType, event_bus
from src.core.models import OrderSide, OrderType, OrderStatus

# Adapters
from src.adapters.kis_adapter import KISAdapter, KISConfig
from src.adapters.kis_websocket import KISWebSocket, KISWebSocketConfig

# Agents
from src.agents.strategy_agent import StrategyAgent, StrategyConfig
from src.agents.position_manager import PositionManager

# Data Layer
from src.data.recorder import data_recorder


class MainController(QObject):
    """
    메인 컨트롤러

    역할:
    1. 시스템 초기화 및 설정 로드
    2. 키움/한투 어댑터 관리
    3. 전략/포지션 에이전트 조율
    4. 주문 실행 및 잔고 동기화
    5. 장 시간 관리
    """

    # Signals
    system_ready = pyqtSignal()
    order_executed = pyqtSignal(dict)

    def __init__(self, config_path: str = "config/settings.yaml"):
        super().__init__()

        self.config_path = config_path
        self.config: Dict[str, Any] = {}

        # 어댑터
        self.kis_adapter: Optional[KISAdapter] = None
        self.kis_websocket: Optional[KISWebSocket] = None  # 실시간 시세
        self.kiwoom_adapter = None  # 별도 프로세스 또는 QThread

        # 에이전트
        self.strategy_agent: Optional[StrategyAgent] = None
        self.position_manager: Optional[PositionManager] = None

        # 상태
        self.is_running = False
        self.is_trading_enabled = False
        self.last_balance_sync: Optional[datetime] = None

        # 타이머
        self.balance_sync_timer = QTimer()
        self.balance_sync_timer.timeout.connect(self._sync_balance)

        self.position_check_timer = QTimer()
        self.position_check_timer.timeout.connect(self._check_positions)

        logger.info("Main Controller initialized")

    # ==================== 초기화 ====================

    def initialize(self) -> bool:
        """
        시스템 초기화

        Returns:
            초기화 성공 여부
        """
        try:
            # 1. 설정 로드
            self._load_config()

            # 2. KIS 어댑터 초기화
            self._init_kis_adapter()

            # 3. KIS 웹소켓 초기화 (실시간 시세)
            self._init_kis_websocket()

            # 4. 에이전트 초기화
            self._init_agents()

            # 5. 이벤트 핸들러 설정
            self._setup_event_handlers()

            # 6. 초기 잔고 동기화
            self._sync_balance()

            logger.info("System initialized successfully")
            self.system_ready.emit()
            return True

        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            return False

    def _load_config(self):
        """설정 파일 로드"""
        config_file = Path(self.config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config not found: {config_file}")

        with open(config_file, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        logger.info(f"Config loaded: {config_file}")

    def _init_kis_adapter(self):
        """KIS 어댑터 초기화"""
        kis_cfg = self.config.get('kis', {})

        kis_config = KISConfig(
            url=kis_cfg.get('url', 'https://openapi.koreainvestment.com:9443'),
            app_key=kis_cfg.get('app_key', ''),
            app_secret=kis_cfg.get('app_secret', ''),
            account_number=kis_cfg.get('account_number', ''),
            account_product_code=kis_cfg.get('account_product_code', '01'),
            hts_id=kis_cfg.get('hts_id', ''),
            cust_type=kis_cfg.get('cust_type', 'P'),
        )

        self.kis_adapter = KISAdapter(kis_config)

        # 연결 테스트
        if not self.kis_adapter.check_connection():
            raise ConnectionError("KIS API connection failed")

        logger.info("KIS Adapter connected")

    def _init_kis_websocket(self):
        """KIS 웹소켓 초기화 (실시간 시세)"""
        kis_cfg = self.config.get('kis', {})

        ws_config = KISWebSocketConfig(
            app_key=kis_cfg.get('app_key', ''),
            app_secret=kis_cfg.get('app_secret', ''),
            rest_url=kis_cfg.get('url', 'https://openapi.koreainvestment.com:9443'),
            ws_url="ws://ops.koreainvestment.com:21000"
        )

        self.kis_websocket = KISWebSocket(ws_config)

        # 가격 콜백 설정
        self.kis_websocket.set_price_callback(self._on_kis_realtime_price)

        # 웹소켓 연결
        if self.kis_websocket.connect():
            logger.info("KIS WebSocket connected")
        else:
            logger.warning("KIS WebSocket connection failed - realtime price disabled")

    def _on_kis_realtime_price(self, stock_code: str, price_data: Dict):
        """KIS 실시간 가격 수신 콜백"""
        current_price = price_data.get("current_price", 0)

        # 포지션 매니저 가격 업데이트
        if self.position_manager:
            self.position_manager.update_price(stock_code, current_price)

    def _init_agents(self):
        """에이전트 초기화"""
        trading_cfg = self.config.get('trading', {})
        exit_cfg = trading_cfg.get('exit', {})

        # 전략 에이전트
        strategy_config = StrategyConfig(
            max_position_per_stock=trading_cfg.get('max_position_per_stock', 0.10),
            max_total_exposure=trading_cfg.get('max_total_exposure', 0.50),
            min_order_amount=trading_cfg.get('min_order_amount', 100000),
            take_profit_pct=exit_cfg.get('take_profit_pct', 0.05),
            stop_loss_pct=exit_cfg.get('stop_loss_pct', 0.02),
            trailing_stop_pct=exit_cfg.get('trailing_stop_pct', 0.015),
        )
        self.strategy_agent = StrategyAgent(strategy_config)

        # 포지션 매니저
        self.position_manager = PositionManager(
            take_profit_pct=exit_cfg.get('take_profit_pct', 0.05),
            stop_loss_pct=exit_cfg.get('stop_loss_pct', 0.02),
            trailing_stop_pct=exit_cfg.get('trailing_stop_pct', 0.015),
            max_hold_minutes=exit_cfg.get('max_hold_minutes', 180),
        )

        logger.info("Agents initialized")

    def _setup_event_handlers(self):
        """이벤트 핸들러 설정"""
        # 매수 신호 처리
        event_bus.subscribe(EventType.STRATEGY_BUY_SIGNAL, self._on_buy_signal)

        # 매도 신호 처리
        event_bus.subscribe(EventType.STRATEGY_SELL_SIGNAL, self._on_sell_signal)
        event_bus.subscribe(EventType.POSITION_TAKE_PROFIT, self._on_sell_signal)
        event_bus.subscribe(EventType.POSITION_STOP_LOSS, self._on_sell_signal)

    # ==================== 시스템 제어 ====================

    def start(self):
        """트레이딩 시작"""
        if self.is_running:
            return

        self.is_running = True
        self.is_trading_enabled = True

        # 시작 전 잔고 동기화 (중요!)
        self._sync_balance()

        # 데이터 기록 시작
        starting_balance = int(self._get_current_balance())
        data_recorder.start(starting_balance=starting_balance)

        # 잔고 동기화 타이머 (1분마다)
        self.balance_sync_timer.start(60000)

        # 포지션 체크 타이머 (10초마다)
        self.position_check_timer.start(10000)

        logger.info("Trading STARTED")

        event_bus.publish(Event(
            type=EventType.SYSTEM_START,
            source="main_controller"
        ))

    def stop(self):
        """트레이딩 중지"""
        self.is_running = False
        self.is_trading_enabled = False

        self.balance_sync_timer.stop()
        self.position_check_timer.stop()

        # 데이터 기록 종료 및 일별 요약 생성
        ending_balance = int(self._get_current_balance())
        data_recorder.stop(ending_balance=ending_balance)

        logger.info("Trading STOPPED")

        event_bus.publish(Event(
            type=EventType.SYSTEM_STOP,
            source="main_controller"
        ))

    def pause_trading(self):
        """매매 일시 중지 (모니터링은 계속)"""
        self.is_trading_enabled = False
        logger.info("Trading PAUSED")

    def resume_trading(self):
        """매매 재개"""
        self.is_trading_enabled = True
        logger.info("Trading RESUMED")

    # ==================== 키움 신호 수신 ====================

    def on_kiwoom_condition_in(self, stock_code: str, stock_name: str, condition_name: str):
        """
        키움 조건 편입 신호 수신

        키움 어댑터에서 호출됨
        """
        event_bus.publish(Event(
            type=EventType.KIWOOM_REALTIME_IN,
            data={
                "stock_code": stock_code,
                "stock_name": stock_name,
                "condition_name": condition_name,
            },
            source="kiwoom"
        ))

    def on_kiwoom_condition_out(self, stock_code: str, stock_name: str, condition_name: str):
        """키움 조건 이탈 신호 수신"""
        event_bus.publish(Event(
            type=EventType.KIWOOM_REALTIME_OUT,
            data={
                "stock_code": stock_code,
                "stock_name": stock_name,
                "condition_name": condition_name,
            },
            source="kiwoom"
        ))

    def on_kiwoom_price_update(self, stock_code: str, current_price: float):
        """키움 실시간 가격 수신"""
        event_bus.publish(Event(
            type=EventType.KIWOOM_PRICE_UPDATE,
            data={
                "stock_code": stock_code,
                "current_price": current_price,
            },
            source="kiwoom"
        ))

    def on_volume_surge_signal(self, stock_code: str, stock_name: str, surge_reason: str):
        """
        거래량 급등 신호 수신 (2차 필터 통과)

        이 메서드가 호출되면 실제 매수 주문이 실행됩니다.
        """
        if not self.is_trading_enabled:
            logger.info(f"Trading disabled, ignoring surge signal: {stock_name}")
            return

        # 이미 보유 중인 종목인지 체크
        if self.position_manager and self.position_manager.has_position(stock_code):
            logger.info(f"Already holding {stock_name}, skip buy")
            return

        logger.info(f"[VOLUME SURGE BUY] {stock_name}({stock_code}) - {surge_reason}")

        # StrategyAgent를 통해 매수 신호 발생
        if self.strategy_agent:
            self.strategy_agent.on_volume_surge(stock_code, stock_name, surge_reason)

    # ==================== 주문 실행 ====================

    def _on_buy_signal(self, event: Event):
        """매수 신호 처리"""
        if not self.is_trading_enabled:
            logger.info("Trading disabled, ignoring buy signal")
            return

        stock_code = event.data.get("stock_code", "")
        stock_name = event.data.get("stock_name", "")
        quantity = event.data.get("quantity", 0)
        reason = event.data.get("reason", "")

        if quantity <= 0:
            return

        logger.info(f"Processing BUY: {stock_name}({stock_code}) x{quantity}")

        try:
            # 현재가 조회
            price_info = self.kis_adapter.get_current_price(stock_code)
            current_price = price_info.current

            if current_price <= 0:
                logger.error(f"Invalid price for {stock_code}")
                return

            # 수량 재계산 (현재가 기준)
            balance = self.kis_adapter.get_account_balance()
            max_amount = balance.available_cash * self.config.get('trading', {}).get('max_position_per_stock', 0.1)
            quantity = min(quantity, int(max_amount / current_price))

            if quantity <= 0:
                logger.info("Insufficient funds for order")
                return

            # 시장가 매수 주문
            order = self.kis_adapter.buy_market(stock_code, quantity)

            if order.status in [order.status.SUBMITTED, order.status.FILLED]:
                logger.info(f"BUY ORDER: {stock_name} x{quantity} - {order.order_id}")

                # 포지션 매니저에 추가
                self.position_manager.add_position(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    quantity=quantity,
                    avg_price=current_price,
                    reason=reason
                )

                self.order_executed.emit({
                    "type": "buy",
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "quantity": quantity,
                    "price": current_price,
                    "order_id": order.order_id
                })

                # 체결 이벤트 발행 (DataRecorder가 기록)
                event_bus.publish(Event(
                    type=EventType.ORDER_FILLED,
                    data={
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "side": "BUY",
                        "filled_qty": quantity,
                        "filled_price": current_price,
                        "condition_name": reason.split(":")[0] if ":" in reason else "",
                        "strategy": reason,
                    },
                    source="main_controller"
                ))

                # 웹소켓 실시간 시세 구독
                if self.kis_websocket and self.kis_websocket.is_connected():
                    self.kis_websocket.subscribe(stock_code)
                    logger.info(f"Subscribed to realtime price: {stock_code}")
            else:
                logger.error(f"Buy order failed: {order.message}")

        except Exception as e:
            logger.error(f"Buy order error: {e}")

    def _on_sell_signal(self, event: Event):
        """매도 신호 처리"""
        if not self.is_trading_enabled:
            logger.info("Trading disabled, ignoring sell signal")
            return

        stock_code = event.data.get("stock_code", "")
        stock_name = event.data.get("stock_name", "")
        quantity = event.data.get("quantity", 0)
        reason = event.data.get("reason", "")

        if quantity <= 0:
            return

        # 매도 전 평균 매수가 조회 (수익 계산용)
        avg_price = 0
        position = self.position_manager.get_position(stock_code)
        if position:
            avg_price = position.avg_price

        logger.info(f"Processing SELL: {stock_name}({stock_code}) x{quantity} - {reason}")

        try:
            # 현재가 조회 (매도 가격)
            price_info = self.kis_adapter.get_current_price(stock_code)
            current_price = price_info.current if price_info else 0

            # 시장가 매도 주문
            order = self.kis_adapter.sell_market(stock_code, quantity)

            if order.status in [order.status.SUBMITTED, order.status.FILLED]:
                logger.info(f"SELL ORDER: {stock_name} x{quantity} - {order.order_id}")

                # 포지션 제거
                self.position_manager.remove_position(stock_code, reason)

                self.order_executed.emit({
                    "type": "sell",
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "quantity": quantity,
                    "order_id": order.order_id,
                    "reason": reason
                })

                # 체결 이벤트 발행 (DataRecorder가 기록)
                event_bus.publish(Event(
                    type=EventType.ORDER_FILLED,
                    data={
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "side": "SELL",
                        "filled_qty": quantity,
                        "filled_price": current_price,
                        "avg_price": avg_price,  # 매수 평균가
                        "strategy": reason,
                    },
                    source="main_controller"
                ))

                # 웹소켓 구독 해제
                if self.kis_websocket and self.kis_websocket.is_connected():
                    self.kis_websocket.unsubscribe(stock_code)
            else:
                logger.error(f"Sell order failed: {order.message}")

        except Exception as e:
            logger.error(f"Sell order error: {e}")

    # ==================== 전체 청산 ====================

    def close_all_positions(self, reason: str = "manual_close_all") -> int:
        """
        전체 포지션 강제 청산 (자동매매 상태와 무관하게 실행)

        Returns:
            청산 주문 실행된 포지션 수
        """
        if not self.position_manager:
            return 0

        positions = self.position_manager.get_all_positions()
        closed_count = 0

        for stock_code, position in list(positions.items()):
            try:
                quantity = position.quantity
                stock_name = position.stock_name

                # 시장가 매도 주문
                order = self.kis_adapter.sell_market(
                    stock_code=stock_code,
                    quantity=quantity
                )

                # 주문 성공 여부: order_id가 있고 status가 SUBMITTED이면 성공
                if order.order_id and order.status == OrderStatus.SUBMITTED:
                    closed_count += 1
                    logger.info(f"Close all - SOLD: {stock_name}({stock_code}) x{quantity} (ID: {order.order_id})")

                    # 포지션 제거
                    self.position_manager.remove_position(stock_code)

                    # UI 알림
                    self.order_executed.emit({
                        "type": "sell",
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "quantity": quantity,
                        "order_id": order.order_id,
                        "reason": reason
                    })

                    # 웹소켓 구독 해제
                    if self.kis_websocket and self.kis_websocket.is_connected():
                        self.kis_websocket.unsubscribe(stock_code)
                else:
                    logger.error(f"Close all - Failed: {stock_name}({stock_code}) - {order.message}")

            except Exception as e:
                logger.error(f"Close all error for {stock_code}: {e}")

        logger.info(f"Close all completed: {closed_count}/{len(positions)} positions sold")
        return closed_count

    # ==================== 잔고 동기화 ====================

    def _sync_balance(self):
        """계좌 잔고 동기화"""
        try:
            balance = self.kis_adapter.get_account_balance()

            # 전략 에이전트 업데이트
            self.strategy_agent.update_balance(
                total_balance=balance.total_balance,
                available_cash=balance.available_cash
            )

            # 포지션 동기화
            self.position_manager.sync_from_balance(balance.positions)

            # 보유 종목 웹소켓 구독 (실시간 시세)
            if self.kis_websocket and self.kis_websocket.is_connected():
                for pos in balance.positions:
                    if pos.stock_code not in self.kis_websocket.get_subscribed_codes():
                        self.kis_websocket.subscribe(pos.stock_code)
                        logger.debug(f"Subscribed to realtime: {pos.stock_code}")

            self.last_balance_sync = datetime.now()

            logger.info(
                f"Balance synced: Total={balance.total_balance:,.0f}, "
                f"Cash={balance.available_cash:,.0f}, "
                f"Positions={len(balance.positions)}"
            )

        except Exception as e:
            logger.error(f"Balance sync error: {e}")

    def _check_positions(self):
        """포지션 상태 체크"""
        positions = self.position_manager.get_all_positions()

        for code, pos in positions.items():
            try:
                # 현재가 조회
                price_info = self.kis_adapter.get_current_price(code)
                current_price = price_info.current

                # 가격 업데이트 (청산 조건 체크 트리거)
                self.position_manager.update_price(code, current_price)

            except Exception as e:
                logger.debug(f"Price check error for {code}: {e}")

    # ==================== 유틸리티 ====================

    def is_market_open(self) -> bool:
        """장 운영 시간 체크"""
        now = datetime.now()

        # 주말 체크
        if now.weekday() >= 5:
            return False

        # 장 시간 체크 (09:00 ~ 15:30)
        market_open = dt_time(9, 0)
        market_close = dt_time(15, 30)
        current_time = now.time()

        return market_open <= current_time <= market_close

    def get_status(self) -> Dict[str, Any]:
        """시스템 상태 조회"""
        positions = self.position_manager.get_all_positions() if self.position_manager else {}

        return {
            "is_running": self.is_running,
            "is_trading_enabled": self.is_trading_enabled,
            "is_market_open": self.is_market_open(),
            "position_count": len(positions),
            "total_exposure": self.position_manager.get_total_exposure() if self.position_manager else 0,
            "total_pnl": self.position_manager.get_total_profit_loss() if self.position_manager else 0,
            "last_balance_sync": self.last_balance_sync,
        }

    def print_status(self):
        """상태 출력"""
        status = self.get_status()

        logger.info("\n" + "=" * 60)
        logger.info("K-HUNTER SYSTEM STATUS")
        logger.info("=" * 60)
        logger.info(f"  Running: {status['is_running']}")
        logger.info(f"  Trading Enabled: {status['is_trading_enabled']}")
        logger.info(f"  Market Open: {status['is_market_open']}")
        logger.info(f"  Positions: {status['position_count']}")
        logger.info(f"  Total Exposure: {status['total_exposure']:,.0f}")
        logger.info(f"  Total P/L: {status['total_pnl']:+,.0f}")
        logger.info(f"  Last Sync: {status['last_balance_sync']}")
        logger.info("=" * 60 + "\n")

        if self.position_manager:
            self.position_manager.print_summary()

    def _get_current_balance(self) -> float:
        """현재 계좌 잔고 조회"""
        try:
            if self.kis_adapter:
                balance = self.kis_adapter.get_account_balance()
                return balance.total_balance
        except Exception as e:
            logger.debug(f"Failed to get balance: {e}")
        return 0
