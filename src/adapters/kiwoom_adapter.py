"""
키움증권 OpenAPI+ Adapter
조건검색 전용 - 실시간 종목 탐색

⚠️ 중요: 32비트 Python + PyQt5 필수
"""

import os
import sys
from collections import deque
from queue import Queue
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, field

from loguru import logger
import time

from src.core.tr_monitor import tr_monitor, TRSource, TRType

# PyQt5 import (32-bit Python 필수)
try:
    from PyQt5.QAxContainer import QAxWidget
    from PyQt5.QtCore import QObject, pyqtSignal, QTimer
    from PyQt5.QtWidgets import QApplication
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False
    logger.warning("PyQt5 not available. Kiwoom adapter requires 32-bit Python with PyQt5.")


@dataclass
class ConditionResult:
    """조건검색 결과"""
    condition_name: str
    stock_codes: List[str]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RealtimeConditionSignal:
    """실시간 조건검색 신호"""
    stock_code: str
    stock_name: str
    condition_name: str
    signal_type: str  # "IN" (편입) or "OUT" (이탈)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class StockPrice:
    """실시간 체결 데이터"""
    stock_code: str
    stock_name: str
    current_price: int
    change_rate: float
    volume: int
    strength: float  # 체결강도
    timestamp: str


class KiwoomAdapter(QObject):
    """
    키움증권 OpenAPI+ 어댑터

    주요 기능:
    - HTS 조건검색 목록 조회
    - 조건검색 실행 (일회성)
    - 실시간 조건검색 (편입/이탈 감지)
    - 실시간 시세 수신

    ⚠️ 32비트 Python 환경에서만 동작
    """

    # PyQt5 Signals
    if PYQT5_AVAILABLE:
        login_completed = pyqtSignal(bool)
        condition_loaded = pyqtSignal()
        condition_result = pyqtSignal(object)  # ConditionResult
        realtime_condition = pyqtSignal(object)  # RealtimeConditionSignal
        realtime_price = pyqtSignal(object)  # StockPrice

    # Rate Limits
    MAX_TR_PER_SEC = 4
    MAX_TR_PER_HOUR = 990

    def __init__(self):
        if not PYQT5_AVAILABLE:
            raise RuntimeError("PyQt5 required. Install with: pip install PyQt5")

        super().__init__()

        self.kiwoom: Optional[QAxWidget] = None
        self.is_connected = False
        self.is_logged_in = False
        self.is_paper_trading = False

        # 계좌 정보
        self.account_list: List[str] = []
        self.using_account: str = ""

        # 조건검색
        self.condition_dict: Dict[str, int] = {}  # 조건명 -> 인덱스
        self.condition_loaded_flag = False

        # 종목 정보
        self.stock_code_to_name: Dict[str, str] = {}
        self.stock_name_to_code: Dict[str, str] = {}

        # 실시간 등록
        self.realtime_registered: set = set()

        # Rate limiting
        self.tr_send_times: deque = deque(maxlen=self.MAX_TR_PER_SEC)
        self.tr_queue: Queue = Queue()

        # Screen numbers
        self._screen_num = 5000

        # Callbacks
        self._on_condition_result: Optional[Callable] = None
        self._on_realtime_condition: Optional[Callable] = None
        self._on_realtime_price: Optional[Callable] = None

        logger.info("Kiwoom Adapter initialized")

    # ==================== 초기화 ====================

    def initialize(self) -> bool:
        """
        키움 OCX 초기화 및 이벤트 연결

        Returns:
            초기화 성공 여부
        """
        try:
            self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
            self._connect_signals()
            logger.info("Kiwoom OCX initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Kiwoom OCX: {e}")
            return False

    def _connect_signals(self):
        """이벤트 핸들러 연결"""
        self.kiwoom.OnEventConnect.connect(self._on_event_connect)
        self.kiwoom.OnReceiveConditionVer.connect(self._on_receive_condition_ver)
        self.kiwoom.OnReceiveTrCondition.connect(self._on_receive_tr_condition)
        self.kiwoom.OnReceiveRealCondition.connect(self._on_receive_real_condition)
        self.kiwoom.OnReceiveRealData.connect(self._on_receive_real_data)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)

    # ==================== 로그인 ====================

    def login(self) -> None:
        """
        로그인 창 열기 (비동기)
        결과는 login_completed 시그널로 전달
        """
        if self.kiwoom is None:
            if not self.initialize():
                return

        ret = self.kiwoom.dynamicCall("CommConnect()")
        if ret == 0:
            logger.info("Login window opened")
        else:
            logger.error(f"Failed to open login window: {ret}")

    def _on_event_connect(self, err_code: int):
        """로그인 결과 처리"""
        if err_code == 0:
            self.is_logged_in = True
            self.is_connected = True
            logger.info("Login successful!")

            # 기본 정보 로드
            self._load_account_info()
            self._check_server_type()
            self._load_stock_codes()
            self._request_condition_list()

            self.login_completed.emit(True)
        else:
            logger.error(f"Login failed: {err_code}")
            self.login_completed.emit(False)

    def _load_account_info(self):
        """계좌 정보 로드"""
        accounts = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        self.account_list = [x for x in accounts.rstrip(';').split(';')
                           if x and not x.endswith('72') and not x.endswith('32')]
        if self.account_list:
            self.using_account = self.account_list[0]
        logger.info(f"Accounts loaded: {len(self.account_list)}")

    def _check_server_type(self):
        """서버 유형 확인 (실전/모의)"""
        server_type = self.kiwoom.dynamicCall("KOA_Functions(QString, QString)", "GetServerGubun", "")
        self.is_paper_trading = (server_type == "1")
        server_name = "모의투자" if self.is_paper_trading else "실전투자"
        logger.info(f"Server type: {server_name}")

    def _load_stock_codes(self):
        """전 종목 코드/이름 로드"""
        kospi = self.kiwoom.dynamicCall("GetCodeListByMarket(QString)", "0").split(';')[:-1]
        kosdaq = self.kiwoom.dynamicCall("GetCodeListByMarket(QString)", "10").split(';')[:-1]

        for code in kospi + kosdaq:
            name = self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code)
            self.stock_code_to_name[code] = name
            self.stock_name_to_code[name] = code

        logger.info(f"Stock codes loaded: {len(self.stock_code_to_name)}")

    # ==================== 조건검색 ====================

    def _request_condition_list(self):
        """조건검색 목록 요청 (내부)"""
        start_time = time.time()
        self.kiwoom.dynamicCall("GetConditionLoad()")
        response_time = (time.time() - start_time) * 1000

        tr_monitor.record(
            source=TRSource.KIWOOM,
            tr_type=TRType.KIWOOM_CONDITION_LIST,
            tr_name="조건검색 목록 요청",
            success=True,
            response_time_ms=response_time
        )
        logger.info("Condition list requested")

    def load_conditions(self):
        """조건검색 목록 요청 (외부 호출용)"""
        self._request_condition_list()

    def _on_receive_condition_ver(self):
        """조건검색 목록 수신"""
        condition_info = self.kiwoom.dynamicCall("GetConditionNameList()")

        self.condition_dict.clear()
        for item in condition_info.split(';'):
            if not item:
                continue
            idx, name = item.split('^')
            self.condition_dict[name] = int(idx)

        self.condition_loaded_flag = True
        logger.info(f"Conditions loaded: {list(self.condition_dict.keys())}")
        self.condition_loaded.emit()

    def get_condition_names(self) -> List[str]:
        """
        사용 가능한 조건검색 목록 반환

        Returns:
            조건명 리스트
        """
        return list(self.condition_dict.keys())

    def search_condition(self, condition_name: str, realtime: bool = False) -> bool:
        """
        조건검색 실행

        Args:
            condition_name: 조건명 (HTS에서 생성)
            realtime: True면 실시간 조건검색, False면 일회성 조회

        Returns:
            요청 성공 여부
        """
        if condition_name not in self.condition_dict:
            logger.error(f"Condition not found: {condition_name}")
            return False

        condition_idx = self.condition_dict[condition_name]
        screen_num = self._get_screen_num()
        search_type = 1 if realtime else 0  # 0: 일회성, 1: 실시간

        start_time = time.time()
        ret = self.kiwoom.dynamicCall(
            "SendCondition(QString, QString, int, int)",
            screen_num, condition_name, condition_idx, search_type
        )
        response_time = (time.time() - start_time) * 1000

        mode = "실시간" if realtime else "일회성"

        if ret == 1:
            tr_monitor.record(
                source=TRSource.KIWOOM,
                tr_type=TRType.KIWOOM_CONDITION_SEARCH,
                tr_name=f"조건검색 ({condition_name[:10]})",
                success=True,
                response_time_ms=response_time,
                details={"condition": condition_name, "realtime": realtime}
            )
            logger.info(f"Condition search started: {condition_name} ({mode})")
            return True
        else:
            tr_monitor.record(
                source=TRSource.KIWOOM,
                tr_type=TRType.KIWOOM_CONDITION_SEARCH,
                tr_name=f"조건검색 ({condition_name[:10]})",
                success=False,
                response_time_ms=response_time,
                error_message=f"SendCondition failed: {ret}"
            )
            logger.error(f"Failed to start condition search: {condition_name}")
            return False

    def stop_condition(self, condition_name: str) -> bool:
        """
        실시간 조건검색 중지

        Args:
            condition_name: 조건명

        Returns:
            요청 성공 여부
        """
        if condition_name not in self.condition_dict:
            return False

        condition_idx = self.condition_dict[condition_name]
        self.kiwoom.dynamicCall(
            "SendConditionStop(QString, QString, int)",
            "4000", condition_name, condition_idx
        )
        logger.info(f"Condition stopped: {condition_name}")
        return True

    def _on_receive_tr_condition(self, scr_no: str, code_list: str,
                                  condition_name: str, index: int, next_flag: int):
        """조건검색 결과 수신 (일회성/초기 목록)"""
        codes = [c for c in code_list.split(';') if len(c) == 6]

        result = ConditionResult(
            condition_name=condition_name,
            stock_codes=codes
        )

        logger.info(f"Condition result: {condition_name} -> {len(codes)} stocks")

        self.condition_result.emit(result)
        if self._on_condition_result:
            self._on_condition_result(result)

    def _on_receive_real_condition(self, code: str, event_type: str,
                                    condition_name: str, condition_idx: str):
        """실시간 조건검색 신호 수신 (편입/이탈)"""
        signal_type = "IN" if event_type == "I" else "OUT"
        stock_name = self.stock_code_to_name.get(code, "")

        signal = RealtimeConditionSignal(
            stock_code=code,
            stock_name=stock_name,
            condition_name=condition_name,
            signal_type=signal_type
        )

        logger.info(f"[{signal_type}] {stock_name}({code}) - {condition_name}")

        self.realtime_condition.emit(signal)
        if self._on_realtime_condition:
            self._on_realtime_condition(signal)

    # ==================== 실시간 시세 ====================

    def register_realtime(self, stock_code: str) -> bool:
        """
        실시간 시세 등록

        Args:
            stock_code: 종목코드

        Returns:
            등록 성공 여부
        """
        if stock_code in self.realtime_registered:
            return True

        fid_list = "10;12;15;20;228"  # 현재가, 등락률, 거래량, 시간, 체결강도
        screen_num = self._get_screen_num()

        # NXT 확장 (실전투자)
        reg_code = stock_code
        if not self.is_paper_trading:
            reg_code = f"{stock_code}_AL"

        start_time = time.time()
        self.kiwoom.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            screen_num, reg_code, fid_list, "1"
        )
        response_time = (time.time() - start_time) * 1000

        tr_monitor.record(
            source=TRSource.KIWOOM,
            tr_type=TRType.KIWOOM_REALTIME_SUBSCRIBE,
            tr_name=f"실시간 등록 ({stock_code})",
            success=True,
            response_time_ms=response_time
        )

        self.realtime_registered.add(stock_code)
        logger.debug(f"Realtime registered: {stock_code}")
        return True

    def unregister_realtime(self, stock_code: str):
        """실시간 시세 해제"""
        if stock_code in self.realtime_registered:
            self.realtime_registered.discard(stock_code)

    def _on_receive_real_data(self, code: str, real_type: str, real_data: str):
        """실시간 데이터 수신"""
        if real_type != "주식체결":
            return

        clean_code = code.replace("_AL", "")

        try:
            price = abs(int(self._get_real_data(real_type, 10)))
            change_rate = float(self._get_real_data(real_type, 12))
            volume = abs(int(self._get_real_data(real_type, 15)))
            time_str = self._get_real_data(real_type, 20).zfill(6)
            strength = float(self._get_real_data(real_type, 228) or 0)

            stock_price = StockPrice(
                stock_code=clean_code,
                stock_name=self.stock_code_to_name.get(clean_code, ""),
                current_price=price,
                change_rate=change_rate,
                volume=volume,
                strength=strength,
                timestamp=time_str
            )

            self.realtime_price.emit(stock_price)
            if self._on_realtime_price:
                self._on_realtime_price(stock_price)

        except Exception as e:
            logger.debug(f"Real data parse error: {e}")

    def _get_real_data(self, real_type: str, fid: int) -> str:
        """실시간 데이터 필드 조회"""
        return self.kiwoom.dynamicCall(
            "GetCommRealData(QString, int)", real_type, fid
        ).replace('-', '')

    # ==================== TR 데이터 ====================

    def _on_receive_tr_data(self, scr_no, rq_name, tr_code, record_name,
                            prev_next, data_len, err_code, msg, splm_msg):
        """TR 데이터 수신"""
        logger.debug(f"TR received: {rq_name}, {tr_code}")

    def _on_receive_msg(self, scr_no, rq_name, tr_code, msg):
        """메시지 수신"""
        logger.debug(f"Message: {msg}")

    # ==================== 유틸리티 ====================

    def _get_screen_num(self) -> str:
        """화면번호 생성 (실시간 시세용)"""
        # 화면번호당 최대 100종목 등록 가능
        # 5000~5099: 실시간 시세용 (100개 화면 x 100종목 = 최대 10,000종목)
        registered_count = len(self.realtime_registered)
        screen_idx = registered_count // 100  # 100종목마다 새 화면번호
        return str(5000 + screen_idx)

    def get_stock_name(self, code: str) -> str:
        """종목코드 -> 종목명"""
        return self.stock_code_to_name.get(code, "")

    def get_stock_code(self, name: str) -> str:
        """종목명 -> 종목코드"""
        return self.stock_name_to_code.get(name, "")

    def is_ready(self) -> bool:
        """사용 준비 완료 여부"""
        return self.is_logged_in and self.condition_loaded_flag

    # ==================== 콜백 설정 ====================

    def set_condition_callback(self, callback: Callable[[ConditionResult], None]):
        """조건검색 결과 콜백 설정"""
        self._on_condition_result = callback

    def set_realtime_condition_callback(self, callback: Callable[[RealtimeConditionSignal], None]):
        """실시간 조건검색 콜백 설정"""
        self._on_realtime_condition = callback

    def set_realtime_price_callback(self, callback: Callable[[StockPrice], None]):
        """실시간 시세 콜백 설정"""
        self._on_realtime_price = callback

    # ==================== 연결 상태 ====================

    def check_connection(self) -> bool:
        """연결 상태 확인"""
        if self.kiwoom is None:
            return False
        state = self.kiwoom.dynamicCall("GetConnectState()")
        return state == 1

    def disconnect(self):
        """연결 종료"""
        if self.kiwoom:
            # 실시간 조건검색 중지
            for name in list(self.condition_dict.keys()):
                self.stop_condition(name)
            logger.info("Kiwoom disconnected")


# ==================== 테스트용 스탠드얼론 실행 ====================

def run_test():
    """테스트 실행 (PyQt5 이벤트 루프 필요)"""
    if not PYQT5_AVAILABLE:
        print("PyQt5 required!")
        return

    app = QApplication(sys.argv)

    adapter = KiwoomAdapter()

    def on_login(success):
        if success:
            print("Login successful!")
            print(f"Conditions: {adapter.get_condition_names()}")
        else:
            print("Login failed!")
            app.quit()

    def on_condition_loaded():
        print(f"Conditions ready: {adapter.get_condition_names()}")
        # 첫 번째 조건검색 실행 (있으면)
        conditions = adapter.get_condition_names()
        if conditions:
            adapter.search_condition(conditions[0], realtime=True)

    def on_condition_result(result: ConditionResult):
        print(f"[{result.condition_name}] {len(result.stock_codes)} stocks found")
        for code in result.stock_codes[:5]:
            name = adapter.get_stock_name(code)
            print(f"  - {name}({code})")

    def on_realtime_signal(signal: RealtimeConditionSignal):
        name = adapter.get_stock_name(signal.stock_code)
        print(f"[{signal.signal_type}] {name}({signal.stock_code}) - {signal.condition_name}")

    adapter.login_completed.connect(on_login)
    adapter.condition_loaded.connect(on_condition_loaded)
    adapter.condition_result.connect(on_condition_result)
    adapter.realtime_condition.connect(on_realtime_signal)

    adapter.login()

    sys.exit(app.exec_())


if __name__ == "__main__":
    run_test()
