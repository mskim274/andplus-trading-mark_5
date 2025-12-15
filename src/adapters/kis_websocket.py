"""
한국투자증권 WebSocket 실시간 시세 Adapter
보유종목 실시간 가격 업데이트용
"""

import json
import threading
import time
import requests
from datetime import datetime
from typing import Optional, Dict, Set, Callable, Any
from dataclasses import dataclass
import websocket
from loguru import logger

from src.core.events import Event, EventType, event_bus


@dataclass
class KISWebSocketConfig:
    """KIS WebSocket 설정"""
    app_key: str
    app_secret: str
    # 실전투자
    rest_url: str = "https://openapi.koreainvestment.com:9443"
    ws_url: str = "ws://ops.koreainvestment.com:21000"


class KISWebSocket:
    """
    한국투자증권 WebSocket 실시간 시세

    Features:
    - 실시간 체결가 수신
    - 자동 재연결
    - 종목 등록/해제
    - 이벤트 버스 연동
    """

    # TR IDs
    TR_ID_PRICE = "H0STCNT0"  # 실시간 체결가

    def __init__(self, config: KISWebSocketConfig):
        self.config = config

        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._is_connected = False
        self._is_running = False

        # 등록된 종목
        self._subscribed_codes: Set[str] = set()

        # 콜백
        self._on_price_callback: Optional[Callable[[str, Dict], None]] = None

        # 재연결 설정
        self._reconnect_delay = 5
        self._max_reconnect_attempts = 10
        self._reconnect_count = 0

        logger.info("KIS WebSocket initialized")

    # ==================== 연결 관리 ====================

    def connect(self) -> bool:
        """WebSocket 연결"""
        if self._is_connected:
            return True

        try:
            # 먼저 approval_key 발급
            approval_key = self._get_approval_key()
            if not approval_key:
                logger.error("Failed to get approval_key, cannot connect WebSocket")
                return False

            self._is_running = True

            self._ws = websocket.WebSocketApp(
                self.config.ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            # 별도 스레드에서 실행
            self._ws_thread = threading.Thread(
                target=self._ws.run_forever,
                daemon=True
            )
            self._ws_thread.start()

            # 연결 대기 (최대 5초)
            for _ in range(50):
                if self._is_connected:
                    logger.info("KIS WebSocket connected successfully")
                    return True
                time.sleep(0.1)

            logger.warning("WebSocket connection timeout")
            return False

        except Exception as e:
            logger.error(f"WebSocket connect error: {e}")
            return False

    def disconnect(self):
        """WebSocket 연결 종료"""
        self._is_running = False

        if self._ws:
            self._ws.close()
            self._ws = None

        self._is_connected = False
        self._subscribed_codes.clear()
        logger.info("KIS WebSocket disconnected")

    def is_connected(self) -> bool:
        """연결 상태"""
        return self._is_connected

    # ==================== 종목 구독 ====================

    def subscribe(self, stock_code: str) -> bool:
        """
        종목 실시간 시세 구독

        Args:
            stock_code: 종목코드 (6자리)
        """
        if not self._is_connected:
            logger.warning("WebSocket not connected, cannot subscribe")
            return False

        stock_code = stock_code.zfill(6)

        if stock_code in self._subscribed_codes:
            return True

        # 구독 요청 메시지
        msg = {
            "header": {
                "approval_key": self._get_approval_key(),
                "custtype": "P",
                "tr_type": "1",  # 1: 등록
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": self.TR_ID_PRICE,
                    "tr_key": stock_code,
                }
            }
        }

        try:
            self._ws.send(json.dumps(msg))
            self._subscribed_codes.add(stock_code)
            logger.debug(f"Subscribed to {stock_code}")
            return True
        except Exception as e:
            logger.error(f"Subscribe error for {stock_code}: {e}")
            return False

    def unsubscribe(self, stock_code: str) -> bool:
        """종목 구독 해제"""
        if not self._is_connected:
            return False

        stock_code = stock_code.zfill(6)

        if stock_code not in self._subscribed_codes:
            return True

        # 해제 요청 메시지
        msg = {
            "header": {
                "approval_key": self._get_approval_key(),
                "custtype": "P",
                "tr_type": "2",  # 2: 해제
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": self.TR_ID_PRICE,
                    "tr_key": stock_code,
                }
            }
        }

        try:
            self._ws.send(json.dumps(msg))
            self._subscribed_codes.discard(stock_code)
            logger.debug(f"Unsubscribed from {stock_code}")
            return True
        except Exception as e:
            logger.error(f"Unsubscribe error for {stock_code}: {e}")
            return False

    def subscribe_multiple(self, stock_codes: list) -> int:
        """여러 종목 구독"""
        success_count = 0
        for code in stock_codes:
            if self.subscribe(code):
                success_count += 1
            time.sleep(0.1)  # 요청 간격
        return success_count

    # ==================== 콜백 설정 ====================

    def set_price_callback(self, callback: Callable[[str, Dict], None]):
        """
        실시간 가격 콜백 설정

        Args:
            callback: callback(stock_code, price_data)
        """
        self._on_price_callback = callback

    # ==================== WebSocket 이벤트 핸들러 ====================

    def _on_open(self, ws):
        """연결 성공"""
        self._is_connected = True
        self._reconnect_count = 0
        logger.info("KIS WebSocket connected")

        # 이벤트 발행
        event_bus.publish(Event(
            type=EventType.SYSTEM_START,
            data={"source": "kis_websocket", "status": "connected"},
            source="kis_websocket"
        ))

    def _on_message(self, ws, message):
        """메시지 수신"""
        try:
            # 실시간 데이터는 | 로 구분된 문자열
            if message.startswith("{"):
                # JSON 응답 (구독 확인 등)
                data = json.loads(message)
                self._handle_json_message(data)
            else:
                # 실시간 시세 데이터
                self._handle_realtime_message(message)

        except Exception as e:
            logger.error(f"Message parse error: {e}")

    def _on_error(self, ws, error):
        """에러 발생"""
        logger.error(f"KIS WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """연결 종료"""
        self._is_connected = False
        logger.info(f"KIS WebSocket closed: {close_status_code} - {close_msg}")

        # 자동 재연결
        if self._is_running and self._reconnect_count < self._max_reconnect_attempts:
            self._reconnect_count += 1
            logger.info(f"Reconnecting in {self._reconnect_delay}s... (attempt {self._reconnect_count})")
            time.sleep(self._reconnect_delay)
            self.connect()

    # ==================== 메시지 처리 ====================

    def _handle_json_message(self, data: Dict):
        """JSON 메시지 처리 (구독 응답 등)"""
        header = data.get("header", {})
        tr_id = header.get("tr_id", "")

        if "PINGPONG" in tr_id:
            # 핑퐁 응답
            self._ws.send(json.dumps(data))
        elif header.get("tr_key"):
            logger.debug(f"Subscription confirmed: {header.get('tr_key')}")

    def _handle_realtime_message(self, message: str):
        """
        실시간 시세 메시지 처리

        포맷: 0|H0STCNT0|4|005930^...^현재가^...
        """
        try:
            parts = message.split("|")
            if len(parts) < 4:
                return

            tr_id = parts[1]
            data_count = int(parts[2])

            # 데이터 파싱
            for i in range(data_count):
                data_part = parts[3 + i] if 3 + i < len(parts) else ""
                fields = data_part.split("^")

                # 디버그: 첫 번째 데이터의 필드 구조 확인 (처음 한번만)
                if not hasattr(self, '_logged_sample') and len(fields) >= 15:
                    self._logged_sample = True
                    logger.info(f"[DEBUG] Sample fields (total {len(fields)}): {fields[:20]}")

                if tr_id == self.TR_ID_PRICE and len(fields) >= 15:
                    self._process_price_data(fields)

        except Exception as e:
            logger.error(f"Realtime message parse error: {e}")

    def _process_price_data(self, fields: list):
        """
        체결가 데이터 처리

        실제 필드 구조 (H0STCNT0 - 실시간 체결):
        0: 유가증권단축종목코드 (예: 036620)
        1: 주식체결시간 (예: 104624 = 10:46:24)
        2: 주식현재가 (예: 6730)
        3: 전일대비부호 (1:상한, 2:상승, 3:보합, 4:하한, 5:하락)
        4: 전일대비 (예: 440)
        5: 전일대비율 (예: 7.00)
        6: 가중평균주식가격
        7: 주식시가
        8: 주식최고가
        9: 주식최저가
        10: 매도호가1
        11: 매수호가1
        12: 체결거래량
        13: 누적거래량
        ...
        """
        try:
            stock_code = fields[0]

            # 안전한 정수 변환 함수
            def safe_int(val, default=0):
                try:
                    return int(float(val)) if val else default
                except (ValueError, TypeError):
                    return default

            # 안전한 실수 변환 함수
            def safe_float(val, default=0.0):
                try:
                    return float(val) if val else default
                except (ValueError, TypeError):
                    return default

            price_data = {
                "stock_code": stock_code,
                "trade_time": fields[1] if len(fields) > 1 else "",
                "current_price": safe_int(fields[2]),      # 현재가
                "change_sign": fields[3] if len(fields) > 3 else "",
                "change": safe_int(fields[4]),              # 전일대비
                "change_rate": safe_float(fields[5]),       # 등락률
                "open_price": safe_int(fields[7]),          # 시가
                "high_price": safe_int(fields[8]),          # 고가
                "low_price": safe_int(fields[9]),           # 저가
                "ask_price": safe_int(fields[10]),          # 매도호가
                "bid_price": safe_int(fields[11]),          # 매수호가
                "trade_volume": safe_int(fields[12]),       # 체결거래량
                "total_volume": safe_int(fields[13]),       # 누적거래량
                "timestamp": datetime.now(),
            }

            # 현재가가 0이면 무시
            if price_data["current_price"] <= 0:
                return

            # 콜백 호출
            if self._on_price_callback:
                self._on_price_callback(stock_code, price_data)

            # 이벤트 발행
            event_bus.publish(Event(
                type=EventType.KIS_REALTIME_PRICE,
                data=price_data,
                source="kis_websocket"
            ))

            logger.debug(f"Realtime price: {stock_code} = {price_data['current_price']:,}")

        except (ValueError, IndexError) as e:
            logger.debug(f"Price data parse error: {e}")

    # ==================== 유틸리티 ====================

    def _get_approval_key(self) -> str:
        """
        WebSocket 접속키 발급 (REST API 호출)

        한투 웹소켓 연결에 필요한 approval_key를 발급받습니다.
        """
        if hasattr(self, '_approval_key') and self._approval_key:
            return self._approval_key

        url = f"{self.config.rest_url}/oauth2/Approval"
        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "secretkey": self.config.app_secret
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()

            self._approval_key = data.get("approval_key", "")
            logger.info(f"WebSocket approval_key acquired")
            return self._approval_key

        except Exception as e:
            logger.error(f"Failed to get approval_key: {e}")
            return ""

    def get_subscribed_codes(self) -> Set[str]:
        """구독 중인 종목 목록"""
        return self._subscribed_codes.copy()

    def get_status(self) -> Dict[str, Any]:
        """상태 조회"""
        return {
            "connected": self._is_connected,
            "subscribed_count": len(self._subscribed_codes),
            "subscribed_codes": list(self._subscribed_codes),
            "reconnect_count": self._reconnect_count,
        }
