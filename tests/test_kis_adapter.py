"""
KIS Adapter 테스트 스크립트
실전계좌 연결 테스트 - 주문은 실행하지 않음
"""

import sys
import os
from pathlib import Path

# 프로젝트 루트 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from loguru import logger

from src.adapters.kis_adapter import KISAdapter, KISConfig


def load_config(config_path: str = "config/settings.yaml") -> dict:
    """설정 파일 로드"""
    full_path = project_root / config_path
    if not full_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {full_path}\n"
            f"Please copy settings.yaml.example to settings.yaml and fill in your credentials."
        )

    with open(full_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_connection(adapter: KISAdapter) -> bool:
    """1. 연결 테스트"""
    logger.info("=" * 50)
    logger.info("TEST 1: Connection Check")
    logger.info("=" * 50)

    result = adapter.check_connection()
    if result:
        logger.success("✓ Connection successful - Token acquired")
    else:
        logger.error("✗ Connection failed")
    return result


def test_balance(adapter: KISAdapter) -> bool:
    """2. 잔고 조회 테스트"""
    logger.info("=" * 50)
    logger.info("TEST 2: Account Balance")
    logger.info("=" * 50)

    try:
        balance = adapter.get_account_balance()

        logger.info(f"총 평가금액: {balance.total_balance:,.0f}원")
        logger.info(f"예수금: {balance.cash_balance:,.0f}원")
        logger.info(f"주식 평가금액: {balance.stock_balance:,.0f}원")
        logger.info(f"총 손익: {balance.total_profit_loss:,.0f}원 ({balance.total_profit_loss_rate:.2f}%)")

        if balance.positions:
            logger.info(f"\n보유 종목 ({len(balance.positions)}개):")
            for pos in balance.positions:
                logger.info(
                    f"  {pos.stock_name}({pos.stock_code}): "
                    f"{pos.quantity}주 @ {pos.avg_price:,.0f}원 "
                    f"→ {pos.current_price:,.0f}원 "
                    f"({pos.profit_loss_rate:+.2f}%)"
                )
        else:
            logger.info("보유 종목 없음")

        logger.success("✓ Balance check successful")
        return True

    except Exception as e:
        logger.error(f"✗ Balance check failed: {e}")
        return False


def test_price(adapter: KISAdapter, stock_code: str = "005930") -> bool:
    """3. 현재가 조회 테스트 (삼성전자)"""
    logger.info("=" * 50)
    logger.info(f"TEST 3: Current Price ({stock_code})")
    logger.info("=" * 50)

    try:
        price = adapter.get_current_price(stock_code)

        logger.info(f"현재가: {price.current:,.0f}원")
        logger.info(f"전일 종가: {price.prev_close:,.0f}원")
        logger.info(f"등락: {price.change:+,.0f}원 ({price.change_rate:+.2f}%)")
        logger.info(f"시가/고가/저가: {price.open:,.0f} / {price.high:,.0f} / {price.low:,.0f}")
        logger.info(f"거래량: {price.volume:,}")

        logger.success("✓ Price check successful")
        return True

    except Exception as e:
        logger.error(f"✗ Price check failed: {e}")
        return False


def test_pending_orders(adapter: KISAdapter) -> bool:
    """4. 미체결 주문 조회 테스트"""
    logger.info("=" * 50)
    logger.info("TEST 4: Pending Orders")
    logger.info("=" * 50)

    try:
        orders = adapter.get_pending_orders()

        if orders:
            logger.info(f"미체결 주문 ({len(orders)}건):")
            for order in orders:
                logger.info(
                    f"  {order['order_id']}: {order['stock_code']} "
                    f"x{order['quantity']} @ {order['price']:,.0f}원"
                )
        else:
            logger.info("미체결 주문 없음")

        logger.success("✓ Pending orders check successful")
        return True

    except Exception as e:
        logger.error(f"✗ Pending orders check failed: {e}")
        return False


def test_buyable_amount(adapter: KISAdapter, stock_code: str = "005930") -> bool:
    """5. 매수 가능 수량 계산 테스트"""
    logger.info("=" * 50)
    logger.info(f"TEST 5: Buyable Amount ({stock_code})")
    logger.info("=" * 50)

    try:
        price = adapter.get_current_price(stock_code)
        buyable = adapter.get_buyable_amount(stock_code, price.current)

        logger.info(f"현재가: {price.current:,.0f}원")
        logger.info(f"매수 가능 수량: {buyable}주")
        logger.info(f"예상 매수 금액: {buyable * price.current:,.0f}원")

        logger.success("✓ Buyable amount check successful")
        return True

    except Exception as e:
        logger.error(f"✗ Buyable amount check failed: {e}")
        return False


def run_all_tests():
    """전체 테스트 실행"""
    logger.info("\n" + "=" * 60)
    logger.info("K-HUNTER KIS ADAPTER TEST")
    logger.info("=" * 60 + "\n")

    # 설정 로드
    try:
        config = load_config()
        kis_config = KISConfig(
            url=config["kis"]["url"],
            app_key=config["kis"]["app_key"],
            app_secret=config["kis"]["app_secret"],
            account_number=config["kis"]["account_number"],
            account_product_code=config["kis"]["account_product_code"],
            hts_id=config["kis"].get("hts_id", ""),
            cust_type=config["kis"].get("cust_type", "P"),
        )
    except FileNotFoundError as e:
        logger.error(str(e))
        return
    except Exception as e:
        logger.error(f"Config error: {e}")
        return

    # 어댑터 생성
    adapter = KISAdapter(kis_config)

    # 테스트 실행
    results = {
        "연결": test_connection(adapter),
        "잔고조회": test_balance(adapter),
        "현재가": test_price(adapter),
        "미체결": test_pending_orders(adapter),
        "매수가능": test_buyable_amount(adapter),
    }

    # 결과 요약
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)

    passed = sum(results.values())
    total = len(results)

    for name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        logger.info(f"  {name}: {status}")

    logger.info(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        logger.success("\n🎉 All tests passed! KIS Adapter is ready.")
    else:
        logger.warning(f"\n⚠️ {total - passed} test(s) failed.")


if __name__ == "__main__":
    # 로그 설정
    logger.remove()
    logger.add(
        sys.stderr,
        format="<level>{level: <8}</level> | {message}",
        level="INFO",
    )

    run_all_tests()
