# -*- coding: utf-8 -*-
"""
키움 어댑터 테스트 스크립트

필수 조건:
1. 32비트 Python 환경
2. 키움 OpenAPI+ 설치
3. HTS에서 조건검색 최소 1개 이상 저장
4. 키움 HTS 로그인 상태 또는 자동 로그인 설정

실행:
    python tests/test_kiwoom_adapter.py
"""

import sys
import os
from pathlib import Path

# UTF-8 출력 설정
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 프로젝트 루트 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger


def check_environment():
    """환경 체크"""
    print("=" * 60)
    print("KIWOOM ADAPTER ENVIRONMENT CHECK")
    print("=" * 60)

    # 1. Python 비트 확인
    import struct
    bits = struct.calcsize("P") * 8
    print(f"\n1. Python: {bits}-bit", end=" ")
    if bits == 32:
        print("[OK]")
    else:
        print("[FAIL] (32-bit required!)")
        print("   Kiwoom OCX requires 32-bit Python.")
        return False

    # 2. PyQt5 확인
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QAxContainer import QAxWidget
        print("2. PyQt5: [OK]")
    except ImportError as e:
        print(f"2. PyQt5: [FAIL] ({e})")
        print("   pip install PyQt5")
        return False

    # 3. 키움 OCX 확인
    try:
        app = QApplication(sys.argv)
        kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        print("3. Kiwoom OCX: [OK]")
        del kiwoom
    except Exception as e:
        print(f"3. Kiwoom OCX: [FAIL] ({e})")
        print("   Install Kiwoom OpenAPI+")
        return False

    print("\nEnvironment check complete! Starting test...\n")
    return True


def run_interactive_test():
    """대화형 테스트 실행"""
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer

    from src.adapters.kiwoom_adapter import (
        KiwoomAdapter,
        ConditionResult,
        RealtimeConditionSignal
    )

    app = QApplication(sys.argv)
    adapter = KiwoomAdapter()

    # 상태 변수
    test_results = {
        "login": False,
        "conditions": False,
        "search": False,
    }

    def on_login(success):
        test_results["login"] = success
        if success:
            print("\n[OK] Login successful!")
            print(f"  - Account: {adapter.using_account[:4]}****")
            print(f"  - Server: {'Paper' if adapter.is_paper_trading else 'Real'}")
            print(f"  - Stocks: {len(adapter.stock_code_to_name)}")
        else:
            print("\n[FAIL] Login failed!")
            QTimer.singleShot(1000, app.quit)

    def on_condition_loaded():
        conditions = adapter.get_condition_names()
        test_results["conditions"] = len(conditions) > 0

        print(f"\n[OK] Condition list loaded!")
        if conditions:
            print(f"  Conditions ({len(conditions)}):")
            for name in conditions:
                print(f"    - {name}")

            # 첫 번째 조건으로 실시간 검색 시작
            print(f"\n>> Starting realtime search: '{conditions[0]}'...")
            adapter.search_condition(conditions[0], realtime=True)
        else:
            print("  [WARN] No conditions found.")
            print("         Please save conditions in Kiwoom HTS first.")
            print_summary()

    def on_condition_result(result: ConditionResult):
        test_results["search"] = True
        print(f"\n[OK] Condition result: {result.condition_name}")
        print(f"  Found {len(result.stock_codes)} stocks:")

        for code in result.stock_codes[:10]:
            name = adapter.get_stock_name(code)
            print(f"    - {name}({code})")

        if len(result.stock_codes) > 10:
            print(f"    ... and {len(result.stock_codes) - 10} more")

        print("\n>> Waiting for realtime signals... (30 sec timeout)")
        QTimer.singleShot(30000, print_summary)

    def on_realtime_signal(signal: RealtimeConditionSignal):
        name = adapter.get_stock_name(signal.stock_code)
        status = "[IN]" if signal.signal_type == "IN" else "[OUT]"
        print(f"  {status} {name}({signal.stock_code}) - {signal.condition_name}")

    def print_summary():
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)

        for name, result in test_results.items():
            status = "[PASS]" if result else "[FAIL]"
            print(f"  {name}: {status}")

        passed = sum(test_results.values())
        total = len(test_results)
        print(f"\nTotal: {passed}/{total}")

        if passed == total:
            print("\nAll tests passed!")
        else:
            print("\nSome tests failed.")

        # 정리
        adapter.disconnect()
        QTimer.singleShot(1000, app.quit)

    # 이벤트 연결
    adapter.login_completed.connect(on_login)
    adapter.condition_loaded.connect(on_condition_loaded)
    adapter.condition_result.connect(on_condition_result)
    adapter.realtime_condition.connect(on_realtime_signal)

    # 로그인 시작
    print("Logging in to Kiwoom...")
    adapter.login()

    sys.exit(app.exec_())


def main():
    """메인 함수"""
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    print("\n" + "=" * 60)
    print("K-HUNTER KIWOOM ADAPTER TEST")
    print("=" * 60 + "\n")

    if not check_environment():
        print("\nPlease check your environment.")
        sys.exit(1)

    run_interactive_test()


if __name__ == "__main__":
    main()
