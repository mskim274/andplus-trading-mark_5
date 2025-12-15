"""
한국투자증권 REST API Adapter
실전투자 전용 - 토큰 관리, 잔고 조회, 주문 실행
"""

import json
import time
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
import requests
from loguru import logger

from src.core.models import (
    Order, OrderSide, OrderType, OrderStatus,
    Position, AccountBalance, Price, StockInfo
)
from src.core.exceptions import (
    KISAPIException, KISAuthenticationError, KISOrderError,
    KISRateLimitError, KISConnectionError
)
from src.core.tr_monitor import tr_monitor, TRSource, TRType


@dataclass
class KISConfig:
    """KIS API 설정"""
    url: str
    app_key: str
    app_secret: str
    account_number: str
    account_product_code: str = "01"
    hts_id: str = ""
    cust_type: str = "P"


class KISAdapter:
    """
    한국투자증권 REST API 어댑터

    Features:
    - 자동 토큰 관리 (발급/갱신)
    - Rate limiting (5 calls/sec)
    - 잔고 조회, 현재가 조회
    - 매수/매도 주문
    - 주문 취소/정정
    """

    # API Endpoints
    TOKEN_URL = "/oauth2/tokenP"
    BALANCE_URL = "/uapi/domestic-stock/v1/trading/inquire-balance"
    PRICE_URL = "/uapi/domestic-stock/v1/quotations/inquire-price"
    ORDER_URL = "/uapi/domestic-stock/v1/trading/order-cash"
    ORDER_REVISE_URL = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
    ORDERS_URL = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
    HASH_URL = "/uapi/hashkey"

    # TR IDs (실전투자용)
    TR_ID_BALANCE = "TTTC8434R"
    TR_ID_PRICE = "FHKST01010100"
    TR_ID_BUY = "TTTC0012U"
    TR_ID_SELL = "TTTC0011U"
    TR_ID_REVISE = "TTTC0013U"
    TR_ID_ORDERS = "TTTC8036R"

    def __init__(self, config: KISConfig):
        """
        Args:
            config: KIS API 설정
        """
        self.config = config
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._last_call_time: float = 0
        self._min_call_interval: float = 0.2  # 5 calls/sec = 200ms interval

        self._base_headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
        }

        logger.info(f"KIS Adapter initialized - Account: {config.account_number[:4]}****")

    # ==================== 토큰 관리 ====================

    def _ensure_token(self) -> None:
        """토큰 유효성 확인 및 필요시 갱신"""
        if self._access_token is None:
            self._request_token()
        elif self._token_expires_at and datetime.now() >= self._token_expires_at:
            logger.info("Token expired, refreshing...")
            self._request_token()

    def _request_token(self) -> None:
        """액세스 토큰 발급"""
        url = f"{self.config.url}{self.TOKEN_URL}"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
        }

        start_time = time.time()
        try:
            response = requests.post(url, json=payload, headers=self._base_headers)
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            # 토큰 유효기간: 보통 24시간, 안전하게 23시간으로 설정
            self._token_expires_at = datetime.now() + timedelta(hours=23)

            response_time = (time.time() - start_time) * 1000
            tr_monitor.record(
                source=TRSource.KIS,
                tr_type=TRType.KIS_TOKEN,
                tr_name="토큰 발급",
                success=True,
                response_time_ms=response_time
            )

            logger.info(f"Token acquired, expires at: {self._token_expires_at}")

        except requests.exceptions.RequestException as e:
            response_time = (time.time() - start_time) * 1000
            tr_monitor.record(
                source=TRSource.KIS,
                tr_type=TRType.KIS_TOKEN,
                tr_name="토큰 발급",
                success=False,
                response_time_ms=response_time,
                error_message=str(e)
            )
            raise KISAuthenticationError(f"Token request failed: {e}")
        except KeyError as e:
            response_time = (time.time() - start_time) * 1000
            tr_monitor.record(
                source=TRSource.KIS,
                tr_type=TRType.KIS_TOKEN,
                tr_name="토큰 발급",
                success=False,
                response_time_ms=response_time,
                error_message=f"Invalid response: {e}"
            )
            raise KISAuthenticationError(f"Invalid token response: {e}")

    def _get_auth_headers(self) -> Dict[str, str]:
        """인증 헤더 생성"""
        self._ensure_token()
        headers = self._base_headers.copy()
        headers["authorization"] = f"Bearer {self._access_token}"
        headers["appkey"] = self.config.app_key
        headers["appsecret"] = self.config.app_secret
        return headers

    # ==================== Rate Limiting ====================

    def _rate_limit(self) -> None:
        """API 호출 속도 제한 (5 calls/sec)"""
        current_time = time.time()
        elapsed = current_time - self._last_call_time
        if elapsed < self._min_call_interval:
            sleep_time = self._min_call_interval - elapsed
            time.sleep(sleep_time)
        self._last_call_time = time.time()

    # ==================== Hash Key ====================

    def _get_hash_key(self, params: Dict[str, Any]) -> str:
        """주문용 해시키 발급"""
        url = f"{self.config.url}{self.HASH_URL}"
        headers = self._get_auth_headers()

        try:
            response = requests.post(url, json=params, headers=headers)
            response.raise_for_status()
            return response.json().get("HASH", "")
        except Exception as e:
            logger.warning(f"Hash key request failed: {e}")
            return ""

    # ==================== API 호출 ====================

    def _get_tr_type_from_id(self, tr_id: str) -> TRType:
        """TR ID로부터 TRType 결정"""
        tr_type_map = {
            self.TR_ID_BALANCE: TRType.KIS_BALANCE,
            self.TR_ID_PRICE: TRType.KIS_PRICE,
            self.TR_ID_BUY: TRType.KIS_ORDER_BUY,
            self.TR_ID_SELL: TRType.KIS_ORDER_SELL,
            self.TR_ID_REVISE: TRType.KIS_OTHER,
            self.TR_ID_ORDERS: TRType.KIS_OTHER,
        }
        return tr_type_map.get(tr_id, TRType.KIS_OTHER)

    def _get_tr_name_from_id(self, tr_id: str) -> str:
        """TR ID로부터 TR 이름 결정"""
        tr_name_map = {
            self.TR_ID_BALANCE: "잔고 조회",
            self.TR_ID_PRICE: "현재가 조회",
            self.TR_ID_BUY: "매수 주문",
            self.TR_ID_SELL: "매도 주문",
            self.TR_ID_REVISE: "주문 정정/취소",
            self.TR_ID_ORDERS: "미체결 조회",
        }
        return tr_name_map.get(tr_id, tr_id)

    def _request(
        self,
        method: str,
        endpoint: str,
        tr_id: str,
        params: Optional[Dict] = None,
        use_hash: bool = False
    ) -> Dict[str, Any]:
        """
        API 요청 실행

        Args:
            method: HTTP method (GET/POST)
            endpoint: API endpoint
            tr_id: Transaction ID
            params: Request parameters
            use_hash: 해시키 사용 여부 (주문 시)

        Returns:
            API response dict
        """
        self._rate_limit()

        url = f"{self.config.url}{endpoint}"
        headers = self._get_auth_headers()
        headers["tr_id"] = tr_id
        headers["custtype"] = self.config.cust_type

        start_time = time.time()
        tr_type = self._get_tr_type_from_id(tr_id)
        tr_name = self._get_tr_name_from_id(tr_id)

        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params)
            else:
                if use_hash and params:
                    headers["hashkey"] = self._get_hash_key(params)
                response = requests.post(url, headers=headers, json=params)

            response.raise_for_status()
            data = response.json()

            # API 응답 확인
            rt_cd = data.get("rt_cd", "")
            if rt_cd != "0":
                msg_cd = data.get("msg_cd", "")
                msg = data.get("msg1", "Unknown error")

                response_time = (time.time() - start_time) * 1000
                tr_monitor.record(
                    source=TRSource.KIS,
                    tr_type=tr_type,
                    tr_name=tr_name,
                    success=False,
                    response_time_ms=response_time,
                    error_message=f"[{msg_cd}] {msg}"
                )
                raise KISAPIException(msg, error_code=msg_cd, response=data)

            response_time = (time.time() - start_time) * 1000
            tr_monitor.record(
                source=TRSource.KIS,
                tr_type=tr_type,
                tr_name=tr_name,
                success=True,
                response_time_ms=response_time,
                details={"tr_id": tr_id}
            )

            return data

        except requests.exceptions.Timeout:
            response_time = (time.time() - start_time) * 1000
            tr_monitor.record(
                source=TRSource.KIS,
                tr_type=tr_type,
                tr_name=tr_name,
                success=False,
                response_time_ms=response_time,
                error_message="Request timeout"
            )
            raise KISConnectionError("Request timeout")
        except requests.exceptions.ConnectionError:
            response_time = (time.time() - start_time) * 1000
            tr_monitor.record(
                source=TRSource.KIS,
                tr_type=tr_type,
                tr_name=tr_name,
                success=False,
                response_time_ms=response_time,
                error_message="Connection failed"
            )
            raise KISConnectionError("Connection failed")
        except requests.exceptions.HTTPError as e:
            response_time = (time.time() - start_time) * 1000
            error_msg = f"HTTP error: {e}"
            if e.response.status_code == 429:
                error_msg = "Rate limit exceeded"
            tr_monitor.record(
                source=TRSource.KIS,
                tr_type=tr_type,
                tr_name=tr_name,
                success=False,
                response_time_ms=response_time,
                error_message=error_msg
            )
            if e.response.status_code == 429:
                raise KISRateLimitError("Rate limit exceeded")
            raise KISAPIException(f"HTTP error: {e}")

    # ==================== 잔고 조회 ====================

    def get_account_balance(self) -> AccountBalance:
        """
        계좌 잔고 조회

        Returns:
            AccountBalance 객체
        """
        params = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "AFHR_FLPR_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "FUND_STTL_ICLD_YN": "N",
            "INQR_DVSN": "01",
            "OFL_YN": "N",
            "PRCS_DVSN": "01",
            "UNPR_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        data = self._request("GET", self.BALANCE_URL, self.TR_ID_BALANCE, params)

        # 포지션 파싱
        positions = []
        output1 = data.get("output1", [])
        for item in output1:
            qty = int(item.get("hldg_qty", 0))
            if qty > 0:
                positions.append(Position(
                    stock_code=item.get("pdno", ""),
                    stock_name=item.get("prdt_name", ""),
                    quantity=qty,
                    avg_price=float(item.get("pchs_avg_pric", 0)),
                    current_price=float(item.get("prpr", 0)),
                    sellable_qty=int(item.get("ord_psbl_qty", 0)),
                ))

        # 총 잔고 정보
        output2 = data.get("output2", [{}])
        if isinstance(output2, list) and output2:
            summary = output2[0]
        else:
            summary = output2

        return AccountBalance(
            total_balance=float(summary.get("tot_evlu_amt", 0)),
            cash_balance=float(summary.get("dnca_tot_amt", 0)),
            stock_balance=float(summary.get("scts_evlu_amt", 0)),
            total_profit_loss=float(summary.get("evlu_pfls_smtl_amt", 0)),
            total_profit_loss_rate=float(summary.get("evlu_pfls_rt", 0)),
            positions=positions,
        )

    # ==================== 현재가 조회 ====================

    def get_current_price(self, stock_code: str) -> Price:
        """
        종목 현재가 조회

        Args:
            stock_code: 종목코드 (6자리)

        Returns:
            Price 객체
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code.zfill(6),
        }

        data = self._request("GET", self.PRICE_URL, self.TR_ID_PRICE, params)
        output = data.get("output", {})

        return Price(
            current=float(output.get("stck_prpr", 0)),
            open=float(output.get("stck_oprc", 0)),
            high=float(output.get("stck_hgpr", 0)),
            low=float(output.get("stck_lwpr", 0)),
            prev_close=float(output.get("stck_sdpr", 0)),
            change=float(output.get("prdy_vrss", 0)),
            change_rate=float(output.get("prdy_ctrt", 0)),
            volume=int(output.get("acml_vol", 0)),
        )

    def get_stock_info(self, stock_code: str) -> Dict[str, Any]:
        """
        종목 상세 정보 조회

        Args:
            stock_code: 종목코드

        Returns:
            종목 정보 dict
        """
        price = self.get_current_price(stock_code)
        return {
            "code": stock_code,
            "current_price": price.current,
            "change_rate": price.change_rate,
            "volume": price.volume,
        }

    # ==================== 주문 ====================

    def place_order(
        self,
        stock_code: str,
        side: OrderSide,
        quantity: int,
        price: float = 0,
        order_type: OrderType = OrderType.LIMIT
    ) -> Order:
        """
        주문 실행

        Args:
            stock_code: 종목코드
            side: 매수/매도
            quantity: 수량
            price: 가격 (시장가 주문시 0)
            order_type: 주문유형

        Returns:
            Order 객체
        """
        # TR ID 선택
        tr_id = self.TR_ID_BUY if side == OrderSide.BUY else self.TR_ID_SELL

        # 주문 파라미터
        params = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "PDNO": stock_code.zfill(6),
            "ORD_DVSN": order_type.value,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(int(price)),
            "CTAC_TLNO": "",
            "SLL_TYPE": "01",
            "EXCG_ID_DVSN_CD": "KRX",
        }

        order = Order(
            stock_code=stock_code,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
        )

        try:
            data = self._request("POST", self.ORDER_URL, tr_id, params, use_hash=True)
            output = data.get("output", {})

            order.order_id = output.get("ODNO", "")
            order.status = OrderStatus.SUBMITTED
            order.message = data.get("msg1", "")
            order.updated_at = datetime.now()

            logger.info(
                f"Order submitted: {side.value} {stock_code} x{quantity} @ {price} "
                f"(ID: {order.order_id})"
            )

        except KISAPIException as e:
            order.status = OrderStatus.REJECTED
            order.message = str(e)
            logger.error(f"Order rejected: {e}")

        return order

    def buy(
        self,
        stock_code: str,
        quantity: int,
        price: float = 0,
        order_type: OrderType = OrderType.LIMIT
    ) -> Order:
        """매수 주문"""
        return self.place_order(stock_code, OrderSide.BUY, quantity, price, order_type)

    def sell(
        self,
        stock_code: str,
        quantity: int,
        price: float = 0,
        order_type: OrderType = OrderType.LIMIT
    ) -> Order:
        """매도 주문"""
        return self.place_order(stock_code, OrderSide.SELL, quantity, price, order_type)

    def buy_market(self, stock_code: str, quantity: int) -> Order:
        """시장가 매수"""
        return self.buy(stock_code, quantity, 0, OrderType.MARKET)

    def sell_market(self, stock_code: str, quantity: int) -> Order:
        """시장가 매도"""
        return self.sell(stock_code, quantity, 0, OrderType.MARKET)

    # ==================== 미체결 주문 조회/취소 ====================

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """미체결 주문 조회"""
        params = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": "0",
        }

        data = self._request("GET", self.ORDERS_URL, self.TR_ID_ORDERS, params)
        output = data.get("output", [])

        orders = []
        for item in output:
            orders.append({
                "order_id": item.get("odno", ""),
                "stock_code": item.get("pdno", ""),
                "quantity": int(item.get("ord_qty", 0)),
                "price": float(item.get("ord_unpr", 0)),
                "order_time": item.get("ord_tmd", ""),
                "order_branch": item.get("ord_gno_brno", ""),
                "cancelable_qty": int(item.get("psbl_qty", 0)),
            })

        return orders

    def cancel_order(
        self,
        order_id: str,
        quantity: int,
        price: float = 0,
        order_branch: str = "06010"
    ) -> bool:
        """
        주문 취소

        Args:
            order_id: 주문번호
            quantity: 취소 수량
            price: 주문 가격
            order_branch: 주문점 (기본 06010)

        Returns:
            성공 여부
        """
        params = {
            "CANO": self.config.account_number,
            "ACNT_PRDT_CD": self.config.account_product_code,
            "KRX_FWDG_ORD_ORGNO": order_branch,
            "ORGN_ODNO": order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02: 취소
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(int(price)),
            "QTY_ALL_ORD_YN": "Y",
            "EXCG_ID_DVSN_CD": "KRX",
        }

        try:
            self._request("POST", self.ORDER_REVISE_URL, self.TR_ID_REVISE, params, use_hash=True)
            logger.info(f"Order cancelled: {order_id}")
            return True
        except KISAPIException as e:
            logger.error(f"Cancel failed: {e}")
            return False

    def cancel_all_orders(self, skip_codes: Optional[List[str]] = None) -> int:
        """
        모든 미체결 주문 취소

        Args:
            skip_codes: 취소 제외할 종목코드 목록

        Returns:
            취소된 주문 수
        """
        skip_codes = skip_codes or []
        orders = self.get_pending_orders()
        cancelled = 0

        for order in orders:
            if order["stock_code"] in skip_codes:
                continue

            if self.cancel_order(
                order["order_id"],
                order["cancelable_qty"],
                order["price"],
                order["order_branch"]
            ):
                cancelled += 1
            time.sleep(0.1)  # API 부하 방지

        return cancelled

    # ==================== 유틸리티 ====================

    def check_connection(self) -> bool:
        """API 연결 상태 확인"""
        try:
            self._ensure_token()
            return True
        except Exception as e:
            logger.error(f"Connection check failed: {e}")
            return False

    def get_buyable_amount(self, stock_code: str, price: float) -> int:
        """
        매수 가능 수량 계산

        Args:
            stock_code: 종목코드
            price: 매수 가격

        Returns:
            매수 가능 수량
        """
        balance = self.get_account_balance()
        if price <= 0:
            return 0
        return int(balance.available_cash / price)
