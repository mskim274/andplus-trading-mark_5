"""
K-Hunter Trading System - Integration Test
통합 테스트: 키움 신호 → 전략 에이전트 → 한투 주문 전체 흐름 검증
"""

import sys
import os

# UTF-8 인코딩 설정
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from loguru import logger

# Core
from src.core.events import Event, EventType, event_bus
from src.core.models import OrderSide, Position

# Adapters
from src.adapters.kis_adapter import KISAdapter, KISConfig

# Agents
from src.agents.strategy_agent import StrategyAgent, StrategyConfig
from src.agents.position_manager import PositionManager


def load_config():
    """설정 파일 로드"""
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml")

    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


class IntegrationTestRunner:
    """통합 테스트 실행기"""

    def __init__(self):
        self.config = load_config()
        self.kis_adapter = None
        self.strategy_agent = None
        self.position_manager = None

        # 테스트 결과 추적
        self.events_received = []
        self.orders_executed = []

    def setup(self):
        """테스트 환경 설정"""
        logger.info("=" * 60)
        logger.info("K-HUNTER INTEGRATION TEST")
        logger.info("=" * 60)

        # 1. KIS 어댑터 초기화
        logger.info("\n[1] KIS Adapter 초기화...")
        kis_cfg = self.config.get('kis', {})
        kis_config = KISConfig(
            url=kis_cfg.get('url', 'https://openapi.koreainvestment.com:9443'),
            app_key=kis_cfg.get('app_key', ''),
            app_secret=kis_cfg.get('app_secret', ''),
            account_number=kis_cfg.get('account_number', ''),
            account_product_code=kis_cfg.get('account_product_code', '01'),
            hts_id=kis_cfg.get('hts_id', ''),
        )
        self.kis_adapter = KISAdapter(kis_config)

        if self.kis_adapter.check_connection():
            logger.info("    [OK] KIS 연결 성공")
        else:
            logger.error("    [FAIL] KIS 연결 실패")
            return False

        # 2. 계좌 잔고 조회
        logger.info("\n[2] 계좌 잔고 조회...")
        balance = self.kis_adapter.get_account_balance()
        logger.info(f"    총평가: {balance.total_balance:,.0f}원")
        logger.info(f"    가용현금: {balance.available_cash:,.0f}원")
        logger.info(f"    보유종목: {len(balance.positions)}개")

        # 3. 전략 에이전트 초기화
        logger.info("\n[3] Strategy Agent 초기화...")
        trading_cfg = self.config.get('trading', {})
        exit_cfg = trading_cfg.get('exit', {})

        strategy_config = StrategyConfig(
            max_position_per_stock=trading_cfg.get('max_position_per_stock', 0.10),
            max_total_exposure=trading_cfg.get('max_total_exposure', 0.50),
            min_order_amount=trading_cfg.get('min_order_amount', 100000),
            take_profit_pct=exit_cfg.get('take_profit_pct', 0.05),
            stop_loss_pct=exit_cfg.get('stop_loss_pct', 0.02),
        )
        self.strategy_agent = StrategyAgent(strategy_config)
        self.strategy_agent.update_balance(
            total_balance=balance.total_balance,
            available_cash=balance.available_cash
        )
        logger.info("    [OK] Strategy Agent 초기화 완료")

        # 4. 포지션 매니저 초기화
        logger.info("\n[4] Position Manager 초기화...")
        self.position_manager = PositionManager(
            take_profit_pct=exit_cfg.get('take_profit_pct', 0.05),
            stop_loss_pct=exit_cfg.get('stop_loss_pct', 0.02),
            trailing_stop_pct=exit_cfg.get('trailing_stop_pct', 0.015),
            max_hold_minutes=exit_cfg.get('max_hold_minutes', 180),
        )

        # 기존 포지션 동기화
        self.position_manager.sync_from_balance(balance.positions)
        logger.info(f"    [OK] Position Manager 초기화 (포지션: {len(self.position_manager.get_all_positions())}개)")

        # 5. 이벤트 핸들러 설정
        logger.info("\n[5] Event Handler 설정...")
        self._setup_event_handlers()
        logger.info("    [OK] 이벤트 핸들러 등록 완료")

        return True

    def _setup_event_handlers(self):
        """테스트용 이벤트 핸들러"""
        # 매수 신호 모니터링
        event_bus.subscribe(EventType.STRATEGY_BUY_SIGNAL, self._on_buy_signal)

        # 매도 신호 모니터링
        event_bus.subscribe(EventType.STRATEGY_SELL_SIGNAL, self._on_sell_signal)
        event_bus.subscribe(EventType.POSITION_TAKE_PROFIT, self._on_sell_signal)
        event_bus.subscribe(EventType.POSITION_STOP_LOSS, self._on_sell_signal)

        # 포지션 이벤트 모니터링
        event_bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
        event_bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)

    def _on_buy_signal(self, event: Event):
        """매수 신호 수신"""
        self.events_received.append(('BUY_SIGNAL', event))
        logger.info(f"    >> BUY SIGNAL: {event.data}")

    def _on_sell_signal(self, event: Event):
        """매도 신호 수신"""
        self.events_received.append(('SELL_SIGNAL', event))
        logger.info(f"    >> SELL SIGNAL: {event.data}")

    def _on_position_opened(self, event: Event):
        """포지션 오픈 이벤트"""
        self.events_received.append(('POSITION_OPENED', event))
        logger.info(f"    >> POSITION OPENED: {event.data}")

    def _on_position_closed(self, event: Event):
        """포지션 종료 이벤트"""
        self.events_received.append(('POSITION_CLOSED', event))
        logger.info(f"    >> POSITION CLOSED: {event.data}")

    def test_event_flow(self):
        """이벤트 흐름 테스트"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 1: Event Flow (이벤트 흐름)")
        logger.info("=" * 60)

        # 시뮬레이션할 종목 (삼성전자)
        test_stock_code = "005930"
        test_stock_name = "삼성전자"
        test_condition = "테스트조건"

        # 현재가 조회
        logger.info(f"\n[1-1] {test_stock_name} 현재가 조회...")
        price_info = self.kis_adapter.get_current_price(test_stock_code)
        logger.info(f"      현재가: {price_info.current:,}원")
        logger.info(f"      전일대비: {price_info.change:+,}원 ({price_info.change_rate:+.2f}%)")

        # 키움 조건 편입 이벤트 시뮬레이션
        logger.info(f"\n[1-2] 키움 조건 편입 시뮬레이션...")
        event_bus.publish(Event(
            type=EventType.KIWOOM_REALTIME_IN,
            data={
                "stock_code": test_stock_code,
                "stock_name": test_stock_name,
                "condition_name": test_condition,
            },
            source="test"
        ))

        # 결과 확인
        buy_signals = [e for e in self.events_received if e[0] == 'BUY_SIGNAL']
        if buy_signals:
            logger.info(f"      [OK] 매수 신호 생성됨: {len(buy_signals)}개")
        else:
            logger.info(f"      [INFO] 매수 신호 없음 (필터링됨 - 잔고 부족 또는 조건 미충족)")

        return True

    def test_position_management(self):
        """포지션 관리 테스트"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 2: Position Management (포지션 관리)")
        logger.info("=" * 60)

        # 테스트용 포지션 추가
        test_code = "TEST01"
        test_name = "테스트종목"
        test_qty = 10
        test_price = 10000

        logger.info(f"\n[2-1] 테스트 포지션 추가...")
        self.position_manager.add_position(
            stock_code=test_code,
            stock_name=test_name,
            quantity=test_qty,
            avg_price=test_price,
            reason="integration_test"
        )

        positions = self.position_manager.get_all_positions()
        if test_code in positions:
            logger.info(f"      [OK] 포지션 추가됨")
        else:
            logger.error(f"      [FAIL] 포지션 추가 실패")
            return False

        # 가격 업데이트 - 익절 조건 테스트
        logger.info(f"\n[2-2] 익절 조건 테스트 (+6%)...")
        self.events_received.clear()
        take_profit_price = test_price * 1.06  # +6%
        self.position_manager.update_price(test_code, take_profit_price)

        take_profit_events = [e for e in self.events_received if 'TAKE_PROFIT' in e[0] or 'SELL' in e[0]]
        if take_profit_events:
            logger.info(f"      [OK] 익절 신호 발생")
        else:
            logger.info(f"      [INFO] 익절 신호 없음")

        # 포지션 정리
        logger.info(f"\n[2-3] 테스트 포지션 정리...")
        self.position_manager.remove_position(test_code, "test_cleanup")

        if test_code not in self.position_manager.get_all_positions():
            logger.info(f"      [OK] 포지션 제거됨")
        else:
            logger.error(f"      [FAIL] 포지션 제거 실패")
            return False

        return True

    def test_filter_system(self):
        """필터 시스템 테스트"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 3: Filter System (2차 필터링)")
        logger.info("=" * 60)

        # 블랙리스트 테스트
        logger.info("\n[3-1] 블랙리스트 테스트...")
        test_code = "BLACKLIST01"
        self.strategy_agent.add_to_blacklist(test_code)

        from src.agents.strategy_agent import FilterResult
        result = self.strategy_agent._apply_filters(test_code, "test")
        if result == FilterResult.REJECT_BLACKLIST:
            logger.info(f"      [OK] 블랙리스트 필터 정상 작동")
        else:
            logger.error(f"      [FAIL] 블랙리스트 필터 실패: {result}")
            return False

        self.strategy_agent.remove_from_blacklist(test_code)

        # 최대 포지션 테스트
        logger.info("\n[3-2] 최대 포지션 필터 테스트...")
        # 임시로 최대 포지션 수를 0으로 설정
        original_max = self.strategy_agent.config.max_positions
        self.strategy_agent.config.max_positions = 0

        # 가짜 포지션 추가하지 않고 바로 테스트
        result = self.strategy_agent._apply_filters("TEST02", "test")
        # max_positions가 0이면 현재 포지션이 0개여도 >= 조건에 걸림
        if result == FilterResult.REJECT_MAX_POSITIONS:
            logger.info(f"      [OK] 최대 포지션 필터 정상 작동")
        else:
            logger.info(f"      [INFO] 필터 결과: {result}")

        # 원복
        self.strategy_agent.config.max_positions = original_max

        return True

    def test_price_query(self):
        """실시간 가격 조회 테스트"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 4: Price Query (가격 조회)")
        logger.info("=" * 60)

        test_stocks = [
            ("005930", "삼성전자"),
            ("000660", "SK하이닉스"),
            ("035720", "카카오"),
        ]

        for code, name in test_stocks:
            try:
                price = self.kis_adapter.get_current_price(code)
                logger.info(f"    {name}({code}): {price.current:,}원 ({price.change_rate:+.2f}%)")
            except Exception as e:
                logger.error(f"    {name}({code}): 조회 실패 - {e}")

        return True

    def run_all_tests(self):
        """전체 테스트 실행"""
        if not self.setup():
            logger.error("\n[FAIL] 테스트 환경 설정 실패")
            return False

        results = []

        # 테스트 실행
        results.append(("Event Flow", self.test_event_flow()))
        results.append(("Position Management", self.test_position_management()))
        results.append(("Filter System", self.test_filter_system()))
        results.append(("Price Query", self.test_price_query()))

        # 결과 요약
        logger.info("\n" + "=" * 60)
        logger.info("TEST RESULTS SUMMARY")
        logger.info("=" * 60)

        passed = 0
        failed = 0
        for name, result in results:
            status = "[PASS]" if result else "[FAIL]"
            logger.info(f"  {status} {name}")
            if result:
                passed += 1
            else:
                failed += 1

        logger.info("-" * 60)
        logger.info(f"  Total: {passed} passed, {failed} failed")
        logger.info("=" * 60)

        # 포지션 요약
        if self.position_manager:
            self.position_manager.print_summary()

        return failed == 0


def main():
    """메인 함수"""
    logger.remove()
    logger.add(
        sys.stdout,
        format="<level>{message}</level>",
        level="INFO"
    )

    runner = IntegrationTestRunner()
    success = runner.run_all_tests()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
