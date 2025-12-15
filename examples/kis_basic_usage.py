"""
KIS Adapter 기본 사용 예제
실전계좌 - 주의: 실제 주문이 실행됩니다!
"""

import sys
from pathlib import Path

# 프로젝트 루트 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from loguru import logger

from src.adapters.kis_adapter import KISAdapter, KISConfig
from src.core.models import OrderType, OrderSide


def load_adapter() -> KISAdapter:
    """설정 로드 및 어댑터 생성"""
    config_path = project_root / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    kis_config = KISConfig(
        url=config["kis"]["url"],
        app_key=config["kis"]["app_key"],
        app_secret=config["kis"]["app_secret"],
        account_number=config["kis"]["account_number"],
        account_product_code=config["kis"]["account_product_code"],
    )

    return KISAdapter(kis_config)


def example_check_balance():
    """예제 1: 잔고 확인"""
    adapter = load_adapter()

    balance = adapter.get_account_balance()
    print(f"\n💰 계좌 잔고")
    print(f"  예수금: {balance.cash_balance:,.0f}원")
    print(f"  주식 평가: {balance.stock_balance:,.0f}원")
    print(f"  총 평가: {balance.total_balance:,.0f}원")

    if balance.positions:
        print(f"\n📊 보유 종목")
        for pos in balance.positions:
            print(f"  {pos.stock_name}: {pos.quantity}주 ({pos.profit_loss_rate:+.2f}%)")


def example_check_price(stock_code: str = "005930"):
    """예제 2: 현재가 조회"""
    adapter = load_adapter()

    price = adapter.get_current_price(stock_code)
    print(f"\n📈 {stock_code} 현재가")
    print(f"  현재가: {price.current:,.0f}원")
    print(f"  등락률: {price.change_rate:+.2f}%")
    print(f"  거래량: {price.volume:,}")


def example_buy_stock(stock_code: str, quantity: int, price: float):
    """
    예제 3: 매수 주문
    ⚠️ 실제 주문이 실행됩니다!
    """
    adapter = load_adapter()

    print(f"\n🛒 매수 주문: {stock_code} x{quantity} @ {price:,.0f}원")

    # 확인
    confirm = input("정말 실행하시겠습니까? (yes/no): ")
    if confirm.lower() != "yes":
        print("취소됨")
        return

    order = adapter.buy(stock_code, quantity, price, OrderType.LIMIT)

    print(f"\n주문 결과:")
    print(f"  주문번호: {order.order_id}")
    print(f"  상태: {order.status.value}")
    print(f"  메시지: {order.message}")


def example_sell_stock(stock_code: str, quantity: int, price: float):
    """
    예제 4: 매도 주문
    ⚠️ 실제 주문이 실행됩니다!
    """
    adapter = load_adapter()

    print(f"\n💸 매도 주문: {stock_code} x{quantity} @ {price:,.0f}원")

    confirm = input("정말 실행하시겠습니까? (yes/no): ")
    if confirm.lower() != "yes":
        print("취소됨")
        return

    order = adapter.sell(stock_code, quantity, price, OrderType.LIMIT)

    print(f"\n주문 결과:")
    print(f"  주문번호: {order.order_id}")
    print(f"  상태: {order.status.value}")
    print(f"  메시지: {order.message}")


def example_cancel_all():
    """예제 5: 모든 미체결 주문 취소"""
    adapter = load_adapter()

    orders = adapter.get_pending_orders()
    if not orders:
        print("\n미체결 주문 없음")
        return

    print(f"\n📋 미체결 주문 {len(orders)}건:")
    for order in orders:
        print(f"  {order['stock_code']} x{order['quantity']} @ {order['price']:,.0f}")

    confirm = input("모두 취소하시겠습니까? (yes/no): ")
    if confirm.lower() != "yes":
        print("취소됨")
        return

    cancelled = adapter.cancel_all_orders()
    print(f"\n{cancelled}건 취소 완료")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    print("=" * 50)
    print("KIS Adapter 사용 예제")
    print("=" * 50)

    # 안전한 조회 예제만 기본 실행
    example_check_balance()
    example_check_price("005930")  # 삼성전자

    # 아래 주문 예제는 직접 실행 필요
    # example_buy_stock("005930", 1, 50000)
    # example_sell_stock("005930", 1, 55000)
    # example_cancel_all()
