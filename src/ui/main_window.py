"""
K-Hunter Trading System - Main Window
통합 UI: 키움 조건검색 + 한투 주문 + 포지션 모니터링
"""

import sys
import os
from datetime import datetime
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QSplitter, QStatusBar, QMessageBox, QHeaderView,
    QComboBox, QSpinBox, QCheckBox, QTabWidget, QFrame
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QFont, QIcon

from loguru import logger

from src.core.events import Event, EventType, event_bus
from src.core.tr_monitor import tr_monitor, TRSource, TRType, TRRecord


class LogHandler:
    """로그를 QTextEdit에 출력하는 핸들러"""
    def __init__(self, text_widget: QTextEdit):
        self.text_widget = text_widget

    def write(self, message: str):
        if message.strip():
            self.text_widget.append(message.strip())
            # 자동 스크롤
            self.text_widget.verticalScrollBar().setValue(
                self.text_widget.verticalScrollBar().maximum()
            )


class MainWindow(QMainWindow):
    """K-Hunter 메인 윈도우"""

    # 시그널 정의
    log_message = pyqtSignal(str)
    realtime_price_signal = pyqtSignal(str, float)  # stock_code, price

    def __init__(self):
        super().__init__()

        # 컨트롤러 (나중에 설정)
        self.controller = None
        self.kiwoom = None
        self.volume_analyzer = None

        # 상태
        self.is_connected_kis = False
        self.is_connected_kiwoom = False
        self.is_trading = False

        # 조건검색 종목 → 테이블 행 매핑
        self._stock_row_map: Dict[str, int] = {}

        # 급등 신호 발생한 종목 (중복 매수 방지)
        self._surge_sent_stocks: set = set()

        # UI 초기화
        self._init_ui()
        self._setup_timers()

        # 로그 시그널 연결
        self.log_message.connect(self._append_log)

        # 실시간 가격 시그널 연결
        self.realtime_price_signal.connect(self._on_realtime_price_update)

        # 이벤트 버스 구독 (한투 웹소켓 실시간 가격)
        event_bus.subscribe(EventType.KIS_REALTIME_PRICE, self._on_kis_price_event)

    def _init_ui(self):
        """UI 초기화"""
        self.setWindowTitle("K-Hunter Trading System v1.0")
        self.setMinimumSize(1200, 800)

        # 중앙 위젯
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 메인 레이아웃
        main_layout = QVBoxLayout(central_widget)

        # 상단: 연결 상태 + 제어 버튼
        main_layout.addWidget(self._create_control_panel())

        # 중앙: 탭 (계좌/포지션, 조건검색, 설정)
        splitter = QSplitter(Qt.Vertical)

        # 상단 탭
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._create_account_tab(), "계좌/포지션")
        self.tab_widget.addTab(self._create_condition_tab(), "조건검색")
        self.tab_widget.addTab(self._create_monitoring_tab(), "TR모니터링")
        self.tab_widget.addTab(self._create_settings_tab(), "설정")
        splitter.addWidget(self.tab_widget)

        # 하단: 로그
        splitter.addWidget(self._create_log_panel())

        splitter.setSizes([500, 250])
        main_layout.addWidget(splitter)

        # 상태바 (TR 카운터 포함)
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        # TR 카운터 라벨
        self.lbl_kis_tr = QLabel("한투 TR: 0/20")
        self.lbl_kis_tr.setStyleSheet("color: #2196F3; padding: 0 10px;")
        self.statusbar.addPermanentWidget(self.lbl_kis_tr)

        self.lbl_kiwoom_tr = QLabel("키움 TR: 0회")
        self.lbl_kiwoom_tr.setStyleSheet("color: #FF9800; padding: 0 10px;")
        self.statusbar.addPermanentWidget(self.lbl_kiwoom_tr)

        self.lbl_tr_errors = QLabel("에러: 0")
        self.lbl_tr_errors.setStyleSheet("color: #888; padding: 0 10px;")
        self.statusbar.addPermanentWidget(self.lbl_tr_errors)

        self._update_status("시스템 준비 완료")

        # TR 모니터 콜백 등록
        tr_monitor.register_callback(self._on_tr_update)

    def _create_control_panel(self) -> QWidget:
        """상단 제어 패널"""
        group = QGroupBox("시스템 제어")
        layout = QHBoxLayout(group)

        # 연결 상태
        status_frame = QFrame()
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_kis_status = QLabel("● 한투: 미연결")
        self.lbl_kis_status.setStyleSheet("color: red; font-weight: bold;")
        status_layout.addWidget(self.lbl_kis_status)

        self.lbl_kiwoom_status = QLabel("● 키움: 미연결")
        self.lbl_kiwoom_status.setStyleSheet("color: red; font-weight: bold;")
        status_layout.addWidget(self.lbl_kiwoom_status)

        self.lbl_market_status = QLabel("● 장상태: -")
        self.lbl_market_status.setStyleSheet("color: gray;")
        status_layout.addWidget(self.lbl_market_status)

        layout.addWidget(status_frame)
        layout.addStretch()

        # 제어 버튼
        self.btn_connect_kis = QPushButton("한투 연결")
        self.btn_connect_kis.clicked.connect(self._on_connect_kis)
        layout.addWidget(self.btn_connect_kis)

        self.btn_connect_kiwoom = QPushButton("키움 연결")
        self.btn_connect_kiwoom.clicked.connect(self._on_connect_kiwoom)
        layout.addWidget(self.btn_connect_kiwoom)

        layout.addWidget(QLabel("  |  "))

        self.btn_start = QPushButton("▶ 자동매매 시작")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_start.clicked.connect(self._on_start_trading)
        self.btn_start.setEnabled(False)
        layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■ 중지")
        self.btn_stop.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_stop.clicked.connect(self._on_stop_trading)
        self.btn_stop.setEnabled(False)
        layout.addWidget(self.btn_stop)

        self.btn_close_all = QPushButton("전체 청산")
        self.btn_close_all.setStyleSheet("background-color: #FF9800; color: white; padding: 8px 16px;")
        self.btn_close_all.clicked.connect(self._on_close_all)
        self.btn_close_all.setEnabled(False)
        layout.addWidget(self.btn_close_all)

        return group

    def _create_account_tab(self) -> QWidget:
        """계좌/포지션 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 상단: 계좌 요약
        account_group = QGroupBox("계좌 정보")
        account_layout = QHBoxLayout(account_group)

        # 총평가
        self.lbl_total_balance = QLabel("총평가: -")
        self.lbl_total_balance.setFont(QFont("맑은 고딕", 14, QFont.Bold))
        account_layout.addWidget(self.lbl_total_balance)

        # 가용현금
        self.lbl_available_cash = QLabel("가용현금: -")
        self.lbl_available_cash.setFont(QFont("맑은 고딕", 12))
        account_layout.addWidget(self.lbl_available_cash)

        # 총손익
        self.lbl_total_pnl = QLabel("총손익: -")
        self.lbl_total_pnl.setFont(QFont("맑은 고딕", 12))
        account_layout.addWidget(self.lbl_total_pnl)

        # 노출비율
        self.lbl_exposure = QLabel("노출: -")
        account_layout.addWidget(self.lbl_exposure)

        account_layout.addStretch()

        # 새로고침 버튼
        btn_refresh = QPushButton("새로고침")
        btn_refresh.clicked.connect(self._on_refresh_balance)
        account_layout.addWidget(btn_refresh)

        layout.addWidget(account_group)

        # 하단: 포지션 테이블
        position_group = QGroupBox("보유 포지션")
        position_layout = QVBoxLayout(position_group)

        self.tbl_positions = QTableWidget()
        self.tbl_positions.setColumnCount(9)
        self.tbl_positions.setHorizontalHeaderLabels([
            "종목코드", "종목명", "수량", "평균단가", "현재가",
            "평가금액", "손익금액", "손익률", "보유시간"
        ])
        self.tbl_positions.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_positions.setAlternatingRowColors(True)
        self.tbl_positions.setSelectionBehavior(QTableWidget.SelectRows)
        position_layout.addWidget(self.tbl_positions)

        layout.addWidget(position_group)

        return widget

    def _create_condition_tab(self) -> QWidget:
        """조건검색 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 조건검색 선택
        condition_group = QGroupBox("조건검색 설정")
        condition_layout = QHBoxLayout(condition_group)

        condition_layout.addWidget(QLabel("조건식:"))
        self.cmb_conditions = QComboBox()
        self.cmb_conditions.setMinimumWidth(200)
        condition_layout.addWidget(self.cmb_conditions)

        self.chk_realtime = QCheckBox("실시간 감시")
        self.chk_realtime.setChecked(True)
        condition_layout.addWidget(self.chk_realtime)

        self.btn_search = QPushButton("조건검색 시작")
        self.btn_search.clicked.connect(self._on_start_condition)
        self.btn_search.setEnabled(False)
        condition_layout.addWidget(self.btn_search)

        self.btn_stop_search = QPushButton("검색 중지")
        self.btn_stop_search.clicked.connect(self._on_stop_condition)
        self.btn_stop_search.setEnabled(False)
        condition_layout.addWidget(self.btn_stop_search)

        condition_layout.addStretch()
        layout.addWidget(condition_group)

        # 신호 테이블
        signal_group = QGroupBox("실시간 신호")
        signal_layout = QVBoxLayout(signal_group)

        self.tbl_signals = QTableWidget()
        self.tbl_signals.setColumnCount(6)
        self.tbl_signals.setHorizontalHeaderLabels([
            "시간", "종목코드", "종목명", "신호", "조건명", "상태"
        ])
        self.tbl_signals.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_signals.setAlternatingRowColors(True)
        signal_layout.addWidget(self.tbl_signals)

        layout.addWidget(signal_group)

        return widget

    def _create_monitoring_tab(self) -> QWidget:
        """TR 모니터링 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 상단: 통계 요약
        stats_group = QGroupBox("TR 통계")
        stats_layout = QHBoxLayout(stats_group)

        # KIS 통계
        kis_frame = QFrame()
        kis_frame.setStyleSheet("QFrame { background-color: #1e3a5f; border-radius: 5px; padding: 5px; }")
        kis_layout = QVBoxLayout(kis_frame)

        self.lbl_kis_total = QLabel("한투 총 호출: 0회")
        self.lbl_kis_total.setStyleSheet("color: #2196F3; font-weight: bold; font-size: 14px;")
        kis_layout.addWidget(self.lbl_kis_total)

        self.lbl_kis_rate = QLabel("초당 호출: 0/20")
        self.lbl_kis_rate.setStyleSheet("color: #64B5F6;")
        kis_layout.addWidget(self.lbl_kis_rate)

        self.lbl_kis_error_rate = QLabel("에러율: 0.0%")
        self.lbl_kis_error_rate.setStyleSheet("color: #81C784;")
        kis_layout.addWidget(self.lbl_kis_error_rate)

        self.lbl_kis_avg_time = QLabel("평균 응답: 0ms")
        self.lbl_kis_avg_time.setStyleSheet("color: #90CAF9;")
        kis_layout.addWidget(self.lbl_kis_avg_time)

        stats_layout.addWidget(kis_frame)

        # Kiwoom 통계
        kiwoom_frame = QFrame()
        kiwoom_frame.setStyleSheet("QFrame { background-color: #4a3520; border-radius: 5px; padding: 5px; }")
        kiwoom_layout = QVBoxLayout(kiwoom_frame)

        self.lbl_kiwoom_total = QLabel("키움 총 호출: 0회")
        self.lbl_kiwoom_total.setStyleSheet("color: #FF9800; font-weight: bold; font-size: 14px;")
        kiwoom_layout.addWidget(self.lbl_kiwoom_total)

        self.lbl_kiwoom_rate = QLabel("초당 호출: 0회")
        self.lbl_kiwoom_rate.setStyleSheet("color: #FFB74D;")
        kiwoom_layout.addWidget(self.lbl_kiwoom_rate)

        self.lbl_kiwoom_error_rate = QLabel("에러율: 0.0%")
        self.lbl_kiwoom_error_rate.setStyleSheet("color: #81C784;")
        kiwoom_layout.addWidget(self.lbl_kiwoom_error_rate)

        self.lbl_kiwoom_avg_time = QLabel("평균 응답: 0ms")
        self.lbl_kiwoom_avg_time.setStyleSheet("color: #FFCC80;")
        kiwoom_layout.addWidget(self.lbl_kiwoom_avg_time)

        stats_layout.addWidget(kiwoom_frame)

        # 버튼
        btn_frame = QFrame()
        btn_layout = QVBoxLayout(btn_frame)
        btn_refresh_stats = QPushButton("새로고침")
        btn_refresh_stats.clicked.connect(self._refresh_tr_stats)
        btn_layout.addWidget(btn_refresh_stats)

        btn_reset_stats = QPushButton("통계 초기화")
        btn_reset_stats.clicked.connect(self._reset_tr_stats)
        btn_layout.addWidget(btn_reset_stats)

        btn_layout.addStretch()
        stats_layout.addWidget(btn_frame)

        layout.addWidget(stats_group)

        # 중앙: TR 타입별 통계
        type_group = QGroupBox("TR 타입별 호출")
        type_layout = QVBoxLayout(type_group)

        self.tbl_tr_types = QTableWidget()
        self.tbl_tr_types.setColumnCount(5)
        self.tbl_tr_types.setHorizontalHeaderLabels([
            "소스", "TR 타입", "호출 수", "에러 수", "에러율"
        ])
        self.tbl_tr_types.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_tr_types.setAlternatingRowColors(True)
        type_layout.addWidget(self.tbl_tr_types)

        layout.addWidget(type_group)

        # 하단: TR 호출 이력
        history_group = QGroupBox("최근 TR 호출 이력")
        history_layout = QVBoxLayout(history_group)

        self.tbl_tr_history = QTableWidget()
        self.tbl_tr_history.setColumnCount(7)
        self.tbl_tr_history.setHorizontalHeaderLabels([
            "시간", "소스", "TR 타입", "TR 이름", "결과", "응답시간", "에러"
        ])
        self.tbl_tr_history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_tr_history.setAlternatingRowColors(True)
        self.tbl_tr_history.setMaximumHeight(200)
        history_layout.addWidget(self.tbl_tr_history)

        layout.addWidget(history_group)

        return widget

    def _create_settings_tab(self) -> QWidget:
        """설정 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 자금관리 설정
        money_group = QGroupBox("자금 관리")
        money_layout = QHBoxLayout(money_group)

        money_layout.addWidget(QLabel("종목당 최대:"))
        self.spn_max_position = QSpinBox()
        self.spn_max_position.setRange(1, 100)
        self.spn_max_position.setValue(10)
        self.spn_max_position.setSuffix("%")
        money_layout.addWidget(self.spn_max_position)

        money_layout.addWidget(QLabel("총 노출 한도:"))
        self.spn_max_exposure = QSpinBox()
        self.spn_max_exposure.setRange(1, 100)
        self.spn_max_exposure.setValue(50)
        self.spn_max_exposure.setSuffix("%")
        money_layout.addWidget(self.spn_max_exposure)

        money_layout.addWidget(QLabel("최소 주문금액:"))
        self.spn_min_amount = QSpinBox()
        self.spn_min_amount.setRange(10000, 10000000)
        self.spn_min_amount.setValue(100000)
        self.spn_min_amount.setSingleStep(10000)
        self.spn_min_amount.setSuffix("원")
        money_layout.addWidget(self.spn_min_amount)

        money_layout.addStretch()
        layout.addWidget(money_group)

        # 청산 설정
        exit_group = QGroupBox("청산 전략")
        exit_layout = QHBoxLayout(exit_group)

        exit_layout.addWidget(QLabel("익절:"))
        self.spn_take_profit = QSpinBox()
        self.spn_take_profit.setRange(1, 100)
        self.spn_take_profit.setValue(5)
        self.spn_take_profit.setSuffix("%")
        exit_layout.addWidget(self.spn_take_profit)

        exit_layout.addWidget(QLabel("손절:"))
        self.spn_stop_loss = QSpinBox()
        self.spn_stop_loss.setRange(1, 100)
        self.spn_stop_loss.setValue(2)
        self.spn_stop_loss.setSuffix("%")
        exit_layout.addWidget(self.spn_stop_loss)

        exit_layout.addWidget(QLabel("트레일링:"))
        self.spn_trailing = QSpinBox()
        self.spn_trailing.setRange(1, 100)
        self.spn_trailing.setValue(15)
        self.spn_trailing.setSuffix("‰")  # per mille (0.1%)
        exit_layout.addWidget(self.spn_trailing)

        exit_layout.addStretch()
        layout.addWidget(exit_group)

        # 2차 필터 설정 (실시간 거래량)
        filter_group = QGroupBox("2차 필터: 실시간 거래량 급등")
        filter_layout = QVBoxLayout(filter_group)

        # 체크박스 + 설정
        filter_row1 = QHBoxLayout()
        self.chk_volume_filter = QCheckBox("거래량 급등 필터 사용")
        self.chk_volume_filter.setChecked(True)
        filter_row1.addWidget(self.chk_volume_filter)
        filter_row1.addStretch()
        filter_layout.addLayout(filter_row1)

        filter_row2 = QHBoxLayout()
        filter_row2.addWidget(QLabel("1분 거래량 배수:"))
        self.spn_volume_ratio = QSpinBox()
        self.spn_volume_ratio.setRange(1, 20)
        self.spn_volume_ratio.setValue(3)
        self.spn_volume_ratio.setSuffix("배 이상")
        filter_row2.addWidget(self.spn_volume_ratio)

        filter_row2.addWidget(QLabel("   체결강도:"))
        self.spn_strength = QSpinBox()
        self.spn_strength.setRange(100, 500)
        self.spn_strength.setValue(120)
        self.spn_strength.setSuffix("% 이상")
        filter_row2.addWidget(self.spn_strength)

        filter_row2.addStretch()
        filter_layout.addLayout(filter_row2)

        # 설명
        lbl_desc = QLabel("※ 조건검색 편입 후, 실시간 거래량/체결강도 조건을 추가로 확인합니다.")
        lbl_desc.setStyleSheet("color: gray; font-size: 11px;")
        filter_layout.addWidget(lbl_desc)

        layout.addWidget(filter_group)

        layout.addStretch()

        return widget

    def _create_log_panel(self) -> QWidget:
        """로그 패널"""
        group = QGroupBox("시스템 로그")
        layout = QVBoxLayout(group)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("Consolas", 9))
        self.txt_log.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        layout.addWidget(self.txt_log)

        # 로그 제어
        btn_layout = QHBoxLayout()
        btn_clear = QPushButton("로그 지우기")
        btn_clear.clicked.connect(self.txt_log.clear)
        btn_layout.addWidget(btn_clear)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        return group

    def _setup_timers(self):
        """타이머 설정"""
        # UI 업데이트 타이머 (1초)
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self._on_ui_update)
        self.ui_timer.start(1000)

        # 시간 표시 타이머
        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self._update_time)
        self.time_timer.start(1000)

    def _load_settings_to_ui(self):
        """설정 파일 값을 UI에 적용"""
        if not self.controller or not self.controller.config:
            return

        config = self.controller.config
        trading = config.get('trading', {})
        exit_cfg = trading.get('exit', {})

        # 자금 관리 설정
        max_pos = int(trading.get('max_position_per_stock', 0.10) * 100)
        max_exp = int(trading.get('max_total_exposure', 0.50) * 100)
        min_amt = int(trading.get('min_order_amount', 100000))

        self.spn_max_position.setValue(max_pos)
        self.spn_max_exposure.setValue(max_exp)
        self.spn_min_amount.setValue(min_amt)

        # 청산 설정
        take_profit = int(exit_cfg.get('take_profit_pct', 0.05) * 100)
        stop_loss = int(exit_cfg.get('stop_loss_pct', 0.02) * 100)
        trailing = int(exit_cfg.get('trailing_stop_pct', 0.015) * 1000)

        self.spn_take_profit.setValue(take_profit)
        self.spn_stop_loss.setValue(stop_loss)
        self.spn_trailing.setValue(trailing)

        self._log(f"설정 로드됨: 종목당 {max_pos}%, 최소금액 {min_amt:,}원")

    # ==================== 이벤트 핸들러 ====================

    def _on_connect_kis(self):
        """한투 연결"""
        self._log("한투 API 연결 시도...")

        try:
            # MainController 초기화
            if self.controller is None:
                from src.agents.main_controller import MainController
                self.controller = MainController()

            if self.controller.initialize():
                self.is_connected_kis = True
                self.lbl_kis_status.setText("● 한투: 연결됨")
                self.lbl_kis_status.setStyleSheet("color: green; font-weight: bold;")
                self.btn_connect_kis.setEnabled(False)
                self._log("한투 API 연결 성공!")
                self._update_balance_display()
                self._load_settings_to_ui()  # 설정 파일 값을 UI에 적용
                self._check_ready()
            else:
                self._log("한투 API 연결 실패")
                QMessageBox.warning(self, "연결 실패", "한투 API 연결에 실패했습니다.")

        except Exception as e:
            self._log(f"한투 연결 오류: {e}")
            QMessageBox.critical(self, "오류", f"연결 중 오류 발생:\n{e}")

    def _on_connect_kiwoom(self):
        """키움 연결"""
        self._log("키움 OpenAPI 연결 시도...")

        try:
            from src.adapters.kiwoom_adapter import KiwoomAdapter, PYQT5_AVAILABLE

            if not PYQT5_AVAILABLE:
                self._log("PyQt5 QAxContainer를 사용할 수 없습니다.")
                QMessageBox.warning(self, "모듈 오류",
                    "PyQt5 QAxContainer를 로드할 수 없습니다.\n32비트 Python 환경에서 실행해주세요.")
                return

            self.kiwoom = KiwoomAdapter()

            # 시그널 연결
            self.kiwoom.login_completed.connect(self._on_kiwoom_login)
            self.kiwoom.condition_loaded.connect(self._on_condition_loaded)
            self.kiwoom.condition_result.connect(self._on_condition_result)
            self.kiwoom.realtime_condition.connect(self._on_realtime_signal)

            # 로그인
            self.kiwoom.login()
            self._log("키움 로그인 대기 중...")

        except ImportError as e:
            self._log(f"키움 모듈 로드 실패: {e}")
            import traceback
            self._log(traceback.format_exc())
            QMessageBox.warning(self, "모듈 오류",
                f"키움 모듈을 로드할 수 없습니다.\n32비트 Python 환경에서 실행해주세요.\n\n{e}")
        except Exception as e:
            self._log(f"키움 연결 오류: {e}")
            import traceback
            self._log(traceback.format_exc())
            QMessageBox.critical(self, "오류", f"키움 연결 중 오류:\n{e}")

    @pyqtSlot(bool)
    def _on_kiwoom_login(self, success: bool):
        """키움 로그인 결과"""
        if success:
            self.is_connected_kiwoom = True
            self.lbl_kiwoom_status.setText("● 키움: 연결됨")
            self.lbl_kiwoom_status.setStyleSheet("color: green; font-weight: bold;")
            self.btn_connect_kiwoom.setEnabled(False)
            self._log("키움 로그인 성공!")

            # VolumeAnalyzer 초기화
            self._init_volume_analyzer()

            # 실시간 시세 시그널 연결
            self.kiwoom.realtime_price.connect(self._on_realtime_price)

            # 조건검색 목록 로드
            self.kiwoom.load_conditions()
            self._check_ready()
        else:
            self._log("키움 로그인 실패")
            QMessageBox.warning(self, "로그인 실패", "키움 로그인에 실패했습니다.")

    def _init_volume_analyzer(self):
        """VolumeAnalyzer 초기화"""
        from src.agents.volume_analyzer import VolumeAnalyzer

        self.volume_analyzer = VolumeAnalyzer(
            volume_surge_ratio=self.spn_volume_ratio.value(),
            strength_threshold=self.spn_strength.value(),
            lookback_minutes=20,
            min_volume_threshold=1000
        )

        # 급등 감지 콜백
        self.volume_analyzer.set_surge_callback(self._on_volume_surge)
        self._log(f"VolumeAnalyzer 초기화: 거래량 {self.spn_volume_ratio.value()}배, 체결강도 {self.spn_strength.value()}%")

    @pyqtSlot()
    def _on_condition_loaded(self):
        """조건검색 목록 로드 완료"""
        conditions = self.kiwoom.get_condition_names()
        self.cmb_conditions.clear()
        self.cmb_conditions.addItems(conditions)
        self.btn_search.setEnabled(True)
        self._log(f"조건검색 {len(conditions)}개 로드됨")

    def _on_start_trading(self):
        """자동매매 시작"""
        if not self.is_connected_kis:
            QMessageBox.warning(self, "연결 필요", "한투 API에 먼저 연결해주세요.")
            return

        reply = QMessageBox.question(
            self, "자동매매 시작",
            "자동매매를 시작하시겠습니까?\n\n실제 주문이 실행됩니다!",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.controller.start()
            self.is_trading = True
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.btn_close_all.setEnabled(True)
            self._log("=== 자동매매 시작 ===")
            self._update_status("자동매매 실행 중")

    def _on_stop_trading(self):
        """자동매매 중지"""
        self.controller.stop()
        self.is_trading = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._log("=== 자동매매 중지 ===")
        self._update_status("자동매매 중지됨")

    def _on_close_all(self):
        """전체 청산"""
        if not self.controller:
            return

        positions = self.controller.position_manager.get_all_positions() if self.controller.position_manager else {}
        if not positions:
            QMessageBox.information(self, "전체 청산", "청산할 포지션이 없습니다.")
            return

        reply = QMessageBox.question(
            self, "전체 청산",
            f"보유 중인 {len(positions)}개 포지션을 모두 청산하시겠습니까?\n\n"
            "※ 시장가로 즉시 매도됩니다.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self._log("=== 전체 청산 실행 ===")
            closed = self.controller.close_all_positions("manual_close_all")
            self._log(f"청산 완료: {closed}개 포지션")
            self._update_balance_display()

            # 청산 후 버튼 비활성화
            if closed > 0:
                self.btn_close_all.setEnabled(False)

    def _on_start_condition(self):
        """조건검색 시작"""
        if not self.kiwoom:
            return

        condition_name = self.cmb_conditions.currentText()
        if not condition_name:
            return

        # 기존 테이블 및 매핑 초기화
        self.tbl_signals.setRowCount(0)
        self._stock_row_map.clear()
        self._surge_sent_stocks.clear()

        # VolumeAnalyzer 초기화
        if self.volume_analyzer:
            self.volume_analyzer.clear()

        realtime = self.chk_realtime.isChecked()
        success = self.kiwoom.search_condition(condition_name, realtime=realtime)

        if success:
            self._log(f"조건검색 시작: {condition_name} (실시간: {realtime})")
            self.btn_search.setEnabled(False)
            self.btn_stop_search.setEnabled(True)
        else:
            self._log(f"조건검색 시작 실패: {condition_name}")

    def _on_stop_condition(self):
        """조건검색 중지"""
        if not self.kiwoom:
            return

        condition_name = self.cmb_conditions.currentText()
        self.kiwoom.stop_condition(condition_name)
        self._log(f"조건검색 중지: {condition_name}")
        self.btn_search.setEnabled(True)
        self.btn_stop_search.setEnabled(False)

    @pyqtSlot(object)
    def _on_condition_result(self, result):
        """조건검색 결과 (초기 종목 목록)"""
        self._log(f"조건검색 결과: {result.condition_name} - {len(result.stock_codes)}종목")

        # 테이블에 초기 종목 추가
        for stock_code in result.stock_codes:
            stock_name = ""
            if self.kiwoom:
                stock_name = self.kiwoom.get_stock_name(stock_code)

            row = self.tbl_signals.rowCount()
            self.tbl_signals.insertRow(row)
            self.tbl_signals.setItem(row, 0, QTableWidgetItem(datetime.now().strftime("%H:%M:%S")))
            self.tbl_signals.setItem(row, 1, QTableWidgetItem(stock_code))
            self.tbl_signals.setItem(row, 2, QTableWidgetItem(stock_name))
            self.tbl_signals.setItem(row, 3, QTableWidgetItem("편입"))
            self.tbl_signals.setItem(row, 4, QTableWidgetItem(result.condition_name))
            self.tbl_signals.setItem(row, 5, QTableWidgetItem("대기중"))

            # 색상 설정
            self.tbl_signals.item(row, 3).setForeground(QColor("#4CAF50"))
            self.tbl_signals.item(row, 5).setForeground(QColor("#888888"))

            # 종목 → 행 매핑
            self._stock_row_map[stock_code] = row

            # VolumeAnalyzer에 종목명 등록
            if self.volume_analyzer:
                self.volume_analyzer.set_stock_name(stock_code, stock_name)

            # 실시간 시세 등록
            if self.kiwoom:
                self.kiwoom.register_realtime(stock_code)

    @pyqtSlot(object)
    def _on_realtime_signal(self, signal):
        """실시간 조건검색 신호 수신 (편입/이탈)"""
        signal_type = "편입" if signal.signal_type == "I" else "이탈"
        self._log(f"[{signal_type}] {signal.stock_name}({signal.stock_code}) - {signal.condition_name}")

        if signal.signal_type == "I":
            # 편입: 테이블에 추가
            row = self.tbl_signals.rowCount()
            self.tbl_signals.insertRow(row)
            self.tbl_signals.setItem(row, 0, QTableWidgetItem(datetime.now().strftime("%H:%M:%S")))
            self.tbl_signals.setItem(row, 1, QTableWidgetItem(signal.stock_code))
            self.tbl_signals.setItem(row, 2, QTableWidgetItem(signal.stock_name))
            self.tbl_signals.setItem(row, 3, QTableWidgetItem("편입"))
            self.tbl_signals.setItem(row, 4, QTableWidgetItem(signal.condition_name))
            self.tbl_signals.setItem(row, 5, QTableWidgetItem("대기중"))

            self.tbl_signals.item(row, 3).setForeground(QColor("#4CAF50"))
            self.tbl_signals.item(row, 5).setForeground(QColor("#888888"))

            # 매핑 및 실시간 등록
            self._stock_row_map[signal.stock_code] = row
            if self.volume_analyzer:
                self.volume_analyzer.set_stock_name(signal.stock_code, signal.stock_name)
            if self.kiwoom:
                self.kiwoom.register_realtime(signal.stock_code)
        else:
            # 이탈: 테이블에서 상태 업데이트
            if signal.stock_code in self._stock_row_map:
                row = self._stock_row_map[signal.stock_code]
                self.tbl_signals.setItem(row, 3, QTableWidgetItem("이탈"))
                self.tbl_signals.setItem(row, 5, QTableWidgetItem("제외"))
                self.tbl_signals.item(row, 3).setForeground(QColor("#f44336"))
                self.tbl_signals.item(row, 5).setForeground(QColor("#f44336"))

        # MainController에 전달 (자동매매 ON일 때)
        if self.controller and self.is_trading:
            if signal.signal_type == "I":
                self.controller.on_kiwoom_condition_in(
                    signal.stock_code, signal.stock_name, signal.condition_name
                )
            else:
                self.controller.on_kiwoom_condition_out(
                    signal.stock_code, signal.stock_name, signal.condition_name
                )

    @pyqtSlot(object)
    def _on_realtime_price(self, price_data):
        """실시간 시세 수신 → VolumeAnalyzer에 전달"""
        stock_code = price_data.stock_code

        # 조건검색 종목만 처리
        if stock_code not in self._stock_row_map:
            return

        # 테이블에 현재가/체결강도 표시 (상태 컬럼 활용)
        row = self._stock_row_map[stock_code]
        current_status = self.tbl_signals.item(row, 5).text() if self.tbl_signals.item(row, 5) else ""

        # 급등/매수요청 상태가 아닐 때만 감시중 표시
        if not current_status.startswith("급등") and not current_status.startswith("매수"):
            status_text = f"감시중 ({price_data.current_price:,}원, 강도:{price_data.strength:.0f}%)"
            self.tbl_signals.setItem(row, 5, QTableWidgetItem(status_text))
            self.tbl_signals.item(row, 5).setForeground(QColor("#4CAF50"))

        if not self.volume_analyzer:
            return

        # 2차 필터 미사용 시 스킵
        if not self.chk_volume_filter.isChecked():
            return

        # VolumeAnalyzer에 데이터 전달
        self.volume_analyzer.update(
            stock_code=price_data.stock_code,
            price=price_data.current_price,
            volume=price_data.volume,
            strength=price_data.strength
        )

    def _on_volume_surge(self, result):
        """거래량 급등 감지 콜백"""
        stock_code = result.stock_code

        # 조건검색 종목인지 확인
        if stock_code not in self._stock_row_map:
            return  # 현재 조건검색 대상이 아닌 종목은 무시

        # 테이블 상태 업데이트
        row = self._stock_row_map[stock_code]
        self.tbl_signals.setItem(row, 5, QTableWidgetItem(f"급등! {result.surge_reason}"))
        self.tbl_signals.item(row, 5).setForeground(QColor("#FF9800"))

        # 자동매매 OFF면 스킵
        if not self.is_trading:
            return

        # 이미 신호 보낸 종목은 스킵 (중복 매수 방지)
        if stock_code in self._surge_sent_stocks:
            return

        # 컨트롤러 없으면 스킵
        if not self.controller:
            self._log(f"[경고] Controller 없음 - 매수 불가")
            return

        self._log(f"[매수신호] {result.stock_name}({stock_code}) - {result.surge_reason}")

        # 실제 매수 로직 호출
        self.controller.on_volume_surge_signal(
            stock_code,
            result.stock_name,
            result.surge_reason
        )

        # 신호 보낸 종목 기록 + UI 상태 업데이트
        self._surge_sent_stocks.add(stock_code)
        self.tbl_signals.setItem(row, 5, QTableWidgetItem(f"매수요청! {result.surge_reason}"))
        self.tbl_signals.item(row, 5).setForeground(QColor("#2196F3"))

    def _on_refresh_balance(self):
        """잔고 새로고침"""
        if self.controller and self.controller.kis_adapter:
            self._update_balance_display()

    def _on_ui_update(self):
        """UI 주기적 업데이트"""
        if self.controller and self.is_trading:
            self._update_positions_table()

    def _update_time(self):
        """시간 업데이트"""
        now = datetime.now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 장 상태 체크
        if self.controller:
            is_open = self.controller.is_market_open()
            market_status = "장중" if is_open else "장외"
            self.lbl_market_status.setText(f"● {market_status} | {time_str}")
            color = "green" if is_open else "gray"
            self.lbl_market_status.setStyleSheet(f"color: {color};")

    # ==================== UI 업데이트 ====================

    def _update_balance_display(self):
        """잔고 표시 업데이트"""
        if not self.controller or not self.controller.kis_adapter:
            return

        try:
            balance = self.controller.kis_adapter.get_account_balance()

            self.lbl_total_balance.setText(f"총평가: {balance.total_balance:,.0f}원")
            self.lbl_available_cash.setText(f"가용현금: {balance.available_cash:,.0f}원")

            # 포지션 테이블 업데이트
            self.tbl_positions.setRowCount(0)
            total_pnl = 0

            for pos in balance.positions:
                row = self.tbl_positions.rowCount()
                self.tbl_positions.insertRow(row)

                pnl = (pos.current_price - pos.avg_price) * pos.quantity
                pnl_rate = (pos.current_price - pos.avg_price) / pos.avg_price * 100 if pos.avg_price > 0 else 0
                total_pnl += pnl

                self.tbl_positions.setItem(row, 0, QTableWidgetItem(pos.stock_code))
                self.tbl_positions.setItem(row, 1, QTableWidgetItem(pos.stock_name))
                self.tbl_positions.setItem(row, 2, QTableWidgetItem(f"{pos.quantity:,}"))
                self.tbl_positions.setItem(row, 3, QTableWidgetItem(f"{pos.avg_price:,.0f}"))
                self.tbl_positions.setItem(row, 4, QTableWidgetItem(f"{pos.current_price:,.0f}"))
                self.tbl_positions.setItem(row, 5, QTableWidgetItem(f"{pos.current_price * pos.quantity:,.0f}"))
                self.tbl_positions.setItem(row, 6, QTableWidgetItem(f"{pnl:+,.0f}"))
                self.tbl_positions.setItem(row, 7, QTableWidgetItem(f"{pnl_rate:+.2f}%"))
                self.tbl_positions.setItem(row, 8, QTableWidgetItem("-"))

                # 손익 색상
                color = QColor("#4CAF50") if pnl >= 0 else QColor("#f44336")
                self.tbl_positions.item(row, 6).setForeground(color)
                self.tbl_positions.item(row, 7).setForeground(color)

            # 총손익 표시
            pnl_color = "green" if total_pnl >= 0 else "red"
            self.lbl_total_pnl.setText(f"총손익: {total_pnl:+,.0f}원")
            self.lbl_total_pnl.setStyleSheet(f"color: {pnl_color};")

            # 노출 비율
            if balance.total_balance > 0:
                exposure = (balance.total_balance - balance.available_cash) / balance.total_balance * 100
                self.lbl_exposure.setText(f"노출: {exposure:.1f}%")

        except Exception as e:
            self._log(f"잔고 조회 오류: {e}")

    def _on_kis_price_event(self, event: Event):
        """한투 웹소켓 실시간 가격 이벤트 (이벤트 버스에서 호출)"""
        stock_code = event.data.get("stock_code", "")
        current_price = event.data.get("current_price", 0)

        # Qt 시그널로 UI 스레드에서 처리
        self.realtime_price_signal.emit(stock_code, float(current_price))

    @pyqtSlot(str, float)
    def _on_realtime_price_update(self, stock_code: str, current_price: float):
        """실시간 가격 업데이트 (UI 스레드에서 실행)"""
        if not self.controller or not self.controller.position_manager:
            return

        # 포지션 테이블에서 해당 종목 행 찾아 업데이트
        for row in range(self.tbl_positions.rowCount()):
            item = self.tbl_positions.item(row, 0)
            if item and item.text() == stock_code:
                # 현재가 업데이트
                self.tbl_positions.setItem(row, 4, QTableWidgetItem(f"{current_price:,.0f}"))

                # 포지션 정보 가져와서 손익 계산
                positions = self.controller.position_manager.get_all_positions()
                if stock_code in positions:
                    pos = positions[stock_code]
                    # 평가금액, 손익금액, 손익률 업데이트
                    self.tbl_positions.setItem(row, 5, QTableWidgetItem(f"{pos.current_value:,.0f}"))
                    self.tbl_positions.setItem(row, 6, QTableWidgetItem(f"{pos.profit_loss:+,.0f}"))
                    self.tbl_positions.setItem(row, 7, QTableWidgetItem(f"{pos.profit_loss_rate*100:+.2f}%"))

                    # 손익 색상
                    color = QColor("#4CAF50") if pos.profit_loss >= 0 else QColor("#f44336")
                    self.tbl_positions.item(row, 6).setForeground(color)
                    self.tbl_positions.item(row, 7).setForeground(color)
                break

        # 계좌 정보 실시간 업데이트
        self._update_account_summary()

    def _update_account_summary(self):
        """계좌 정보 요약 실시간 업데이트"""
        if not self.controller or not self.controller.position_manager:
            return

        pm = self.controller.position_manager
        positions = pm.get_all_positions()

        # 실시간 주식 평가금액 (현재가 × 수량)
        total_stock_value = sum(pos.current_value for pos in positions.values())

        # 실시간 손익 계산
        total_pnl = sum(pos.profit_loss for pos in positions.values())

        # 가용현금 가져오기
        if self.controller.strategy_agent:
            cash = self.controller.strategy_agent._available_cash
        else:
            cash = 0

        # 총평가 = 가용현금 + 주식평가금액
        realtime_total = cash + total_stock_value

        # UI 업데이트
        self.lbl_total_balance.setText(f"총평가: {realtime_total:,.0f}원")
        self.lbl_available_cash.setText(f"가용현금: {cash:,.0f}원")

        # 총손익 (색상 적용)
        pnl_color = "#4CAF50" if total_pnl >= 0 else "#f44336"
        self.lbl_total_pnl.setText(f"총손익: {total_pnl:+,.0f}원")
        self.lbl_total_pnl.setStyleSheet(f"color: {pnl_color};")

        # 노출비율 (주식평가 / 총평가)
        if realtime_total > 0:
            exposure = (total_stock_value / realtime_total) * 100
            self.lbl_exposure.setText(f"노출: {exposure:.1f}%")

    def _update_positions_table(self):
        """포지션 테이블 업데이트"""
        if not self.controller or not self.controller.position_manager:
            return

        positions = self.controller.position_manager.get_all_positions()

        self.tbl_positions.setRowCount(0)
        for code, pos in positions.items():
            row = self.tbl_positions.rowCount()
            self.tbl_positions.insertRow(row)

            self.tbl_positions.setItem(row, 0, QTableWidgetItem(pos.stock_code))
            self.tbl_positions.setItem(row, 1, QTableWidgetItem(pos.stock_name))
            self.tbl_positions.setItem(row, 2, QTableWidgetItem(f"{pos.quantity:,}"))
            self.tbl_positions.setItem(row, 3, QTableWidgetItem(f"{pos.avg_price:,.0f}"))
            self.tbl_positions.setItem(row, 4, QTableWidgetItem(f"{pos.current_price:,.0f}"))
            self.tbl_positions.setItem(row, 5, QTableWidgetItem(f"{pos.current_value:,.0f}"))
            self.tbl_positions.setItem(row, 6, QTableWidgetItem(f"{pos.profit_loss:+,.0f}"))
            self.tbl_positions.setItem(row, 7, QTableWidgetItem(f"{pos.profit_loss_rate*100:+.2f}%"))
            self.tbl_positions.setItem(row, 8, QTableWidgetItem(f"{pos.hold_time_minutes}분"))

            # 손익 색상
            color = QColor("#4CAF50") if pos.profit_loss >= 0 else QColor("#f44336")
            self.tbl_positions.item(row, 6).setForeground(color)
            self.tbl_positions.item(row, 7).setForeground(color)

    def _check_ready(self):
        """시작 가능 여부 체크"""
        if self.is_connected_kis:
            self.btn_start.setEnabled(True)
            # 보유 포지션이 있으면 전체 청산 버튼 활성화
            if self.controller and self.controller.position_manager:
                positions = self.controller.position_manager.get_all_positions()
                if positions:
                    self.btn_close_all.setEnabled(True)

    def _update_status(self, message: str):
        """상태바 업데이트"""
        self.statusbar.showMessage(message)

    def _log(self, message: str):
        """로그 추가"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_message.emit(f"[{timestamp}] {message}")

    @pyqtSlot(str)
    def _append_log(self, message: str):
        """로그 텍스트 추가"""
        self.txt_log.append(message)

    # ==================== TR 모니터링 ====================

    def _on_tr_update(self, record: TRRecord):
        """TR 호출 콜백 (tr_monitor에서 호출)"""
        # Qt 메인 스레드에서 실행되도록 시그널 사용
        from PyQt5.QtCore import QMetaObject, Q_ARG, Qt as QtCore_Qt
        QMetaObject.invokeMethod(self, "_update_tr_display", QtCore_Qt.QueuedConnection)

    @pyqtSlot()
    def _update_tr_display(self):
        """TR 디스플레이 업데이트 (UI 스레드)"""
        # 상태바 업데이트
        kis_stats = tr_monitor.get_kis_stats()
        kiwoom_stats = tr_monitor.get_kiwoom_stats()

        # 상태바 라벨 업데이트
        rate_color = "#4CAF50" if kis_stats["calls_per_second"] < 15 else "#FF9800" if kis_stats["calls_per_second"] < 18 else "#f44336"
        self.lbl_kis_tr.setText(f"한투 TR: {kis_stats['calls_per_second']}/20")
        self.lbl_kis_tr.setStyleSheet(f"color: {rate_color}; padding: 0 10px;")

        self.lbl_kiwoom_tr.setText(f"키움 TR: {kiwoom_stats['total_calls']}회")

        total_errors = kis_stats["error_count"] + kiwoom_stats["error_count"]
        error_color = "#888" if total_errors == 0 else "#f44336"
        self.lbl_tr_errors.setText(f"에러: {total_errors}")
        self.lbl_tr_errors.setStyleSheet(f"color: {error_color}; padding: 0 10px;")

        # 모니터링 탭이 활성화되어 있으면 상세 업데이트
        if self.tab_widget.currentIndex() == 2:  # TR모니터링 탭
            self._refresh_tr_stats()

    def _refresh_tr_stats(self):
        """TR 통계 새로고침"""
        kis_stats = tr_monitor.get_kis_stats()
        kiwoom_stats = tr_monitor.get_kiwoom_stats()

        # KIS 통계 업데이트
        self.lbl_kis_total.setText(f"한투 총 호출: {kis_stats['total_calls']:,}회")

        rate_pct = kis_stats.get('rate_usage_pct', 0)
        rate_color = "#4CAF50" if rate_pct < 75 else "#FF9800" if rate_pct < 90 else "#f44336"
        self.lbl_kis_rate.setText(f"초당 호출: {kis_stats['calls_per_second']}/20 ({rate_pct:.0f}%)")
        self.lbl_kis_rate.setStyleSheet(f"color: {rate_color};")

        error_rate = kis_stats.get('error_rate', 0)
        error_color = "#81C784" if error_rate < 1 else "#FF9800" if error_rate < 5 else "#f44336"
        self.lbl_kis_error_rate.setText(f"에러율: {error_rate:.1f}% ({kis_stats['error_count']}건)")
        self.lbl_kis_error_rate.setStyleSheet(f"color: {error_color};")

        self.lbl_kis_avg_time.setText(f"평균 응답: {kis_stats.get('avg_response_time', 0):.0f}ms")

        # Kiwoom 통계 업데이트
        self.lbl_kiwoom_total.setText(f"키움 총 호출: {kiwoom_stats['total_calls']:,}회")
        self.lbl_kiwoom_rate.setText(f"초당 호출: {kiwoom_stats['calls_per_second']}회")

        kiwoom_error_rate = kiwoom_stats.get('error_rate', 0)
        kiwoom_error_color = "#81C784" if kiwoom_error_rate < 1 else "#FF9800" if kiwoom_error_rate < 5 else "#f44336"
        self.lbl_kiwoom_error_rate.setText(f"에러율: {kiwoom_error_rate:.1f}% ({kiwoom_stats['error_count']}건)")
        self.lbl_kiwoom_error_rate.setStyleSheet(f"color: {kiwoom_error_color};")

        self.lbl_kiwoom_avg_time.setText(f"평균 응답: {kiwoom_stats.get('avg_response_time', 0):.0f}ms")

        # TR 타입별 테이블 업데이트
        self._update_tr_type_table(kis_stats, kiwoom_stats)

        # 호출 이력 테이블 업데이트
        self._update_tr_history_table()

    def _update_tr_type_table(self, kis_stats: dict, kiwoom_stats: dict):
        """TR 타입별 테이블 업데이트"""
        self.tbl_tr_types.setRowCount(0)

        # KIS 타입별
        for tr_type, data in kis_stats.get('by_type', {}).items():
            row = self.tbl_tr_types.rowCount()
            self.tbl_tr_types.insertRow(row)

            self.tbl_tr_types.setItem(row, 0, QTableWidgetItem("한투"))
            self.tbl_tr_types.setItem(row, 1, QTableWidgetItem(tr_type))
            self.tbl_tr_types.setItem(row, 2, QTableWidgetItem(f"{data['count']:,}"))
            self.tbl_tr_types.setItem(row, 3, QTableWidgetItem(f"{data['errors']}"))

            error_rate = (data['errors'] / data['count'] * 100) if data['count'] > 0 else 0
            self.tbl_tr_types.setItem(row, 4, QTableWidgetItem(f"{error_rate:.1f}%"))

            # 색상
            self.tbl_tr_types.item(row, 0).setForeground(QColor("#2196F3"))
            if data['errors'] > 0:
                self.tbl_tr_types.item(row, 3).setForeground(QColor("#f44336"))
                self.tbl_tr_types.item(row, 4).setForeground(QColor("#f44336"))

        # Kiwoom 타입별
        for tr_type, data in kiwoom_stats.get('by_type', {}).items():
            row = self.tbl_tr_types.rowCount()
            self.tbl_tr_types.insertRow(row)

            self.tbl_tr_types.setItem(row, 0, QTableWidgetItem("키움"))
            self.tbl_tr_types.setItem(row, 1, QTableWidgetItem(tr_type))
            self.tbl_tr_types.setItem(row, 2, QTableWidgetItem(f"{data['count']:,}"))
            self.tbl_tr_types.setItem(row, 3, QTableWidgetItem(f"{data['errors']}"))

            error_rate = (data['errors'] / data['count'] * 100) if data['count'] > 0 else 0
            self.tbl_tr_types.setItem(row, 4, QTableWidgetItem(f"{error_rate:.1f}%"))

            # 색상
            self.tbl_tr_types.item(row, 0).setForeground(QColor("#FF9800"))
            if data['errors'] > 0:
                self.tbl_tr_types.item(row, 3).setForeground(QColor("#f44336"))
                self.tbl_tr_types.item(row, 4).setForeground(QColor("#f44336"))

    def _update_tr_history_table(self):
        """TR 호출 이력 테이블 업데이트"""
        history = tr_monitor.get_recent_history(50)

        self.tbl_tr_history.setRowCount(0)
        for record in reversed(history):  # 최신순
            row = self.tbl_tr_history.rowCount()
            self.tbl_tr_history.insertRow(row)

            self.tbl_tr_history.setItem(row, 0, QTableWidgetItem(record.timestamp.strftime("%H:%M:%S")))
            self.tbl_tr_history.setItem(row, 1, QTableWidgetItem(record.source.value))
            self.tbl_tr_history.setItem(row, 2, QTableWidgetItem(record.tr_type.value))
            self.tbl_tr_history.setItem(row, 3, QTableWidgetItem(record.tr_name))

            result_text = "성공" if record.success else "실패"
            result_item = QTableWidgetItem(result_text)
            result_item.setForeground(QColor("#4CAF50") if record.success else QColor("#f44336"))
            self.tbl_tr_history.setItem(row, 4, result_item)

            self.tbl_tr_history.setItem(row, 5, QTableWidgetItem(f"{record.response_time_ms:.0f}ms"))
            self.tbl_tr_history.setItem(row, 6, QTableWidgetItem(record.error_message or "-"))

            # 소스 색상
            source_color = QColor("#2196F3") if record.source == TRSource.KIS else QColor("#FF9800")
            self.tbl_tr_history.item(row, 1).setForeground(source_color)

    def _reset_tr_stats(self):
        """TR 통계 초기화"""
        reply = QMessageBox.question(
            self, "통계 초기화",
            "TR 통계를 초기화하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            tr_monitor.reset_stats()
            self._refresh_tr_stats()
            self._log("TR 통계가 초기화되었습니다.")

    def closeEvent(self, event):
        """종료 이벤트"""
        if self.is_trading:
            reply = QMessageBox.question(
                self, "종료 확인",
                "자동매매가 실행 중입니다.\n정말 종료하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return

        # TR 모니터 콜백 해제
        tr_monitor.unregister_callback(self._on_tr_update)

        # 정리
        if self.controller:
            self.controller.stop()

        event.accept()
