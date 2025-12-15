# K-Hunter 기술 설계서 v1.0
## 키움-한투 하이브리드 자동매매 시스템

**작성일**: 2025-12-12
**버전**: 1.0
**목적**: 키움증권의 실시간 조건검색 + 한국투자증권 REST API 주문 통합 시스템

---

# 1. Executive Summary

## 1.1 프로젝트 개요

| 항목 | 내용 |
|------|------|
| **프로젝트명** | K-Hunter (Kiwoom Search & KIS Trade Hybrid Bot) |
| **핵심 가치** | 키움의 "검색 능력" + 한투의 "주문 능력" 결합 |
| **목표** | 과매도 종목 실시간 포착 → 자동 매수 → 익절/손절 자동 매도 |

## 1.2 왜 하이브리드인가?

### 키움증권의 강점 (검색)
- **실시간 조건검색 Push**: 서버가 2,500+ 종목 감시 → 조건 만족 시 즉시 통보
- **조건식 자유도**: HTS에서 수백 가지 지표 조합 가능
- **무제한 수신**: 실시간 조건검색은 API 호출 제한 미적용

### 한국투자증권의 강점 (주문)
- **REST API**: HTTP 기반으로 단순하고 빠름 (OCX 대비)
- **모의투자 환경**: 테스트 용이
- **토큰 자동화**: 로그인 자동화 구현 용이

### 하이브리드의 시너지
```
키움 (Eye)          한투 (Hand)
   │                    │
   ▼                    ▼
[실시간 조건 포착] → [즉시 매수 주문]
   │                    │
   └──── Python (Brain) ────┘
         필터링/자금관리
```

---

# 2. 기술적 실현 가능성 분석

## 2.1 핵심 기술 검증 결과

### ✅ 검증 완료: 키움 OCX + 한투 REST 동일 프로세스 통합

**참고 코드 분석 결과**:
- 키움: `PyQt5.QAxContainer.QAxWidget` + `QTimer` 기반 이벤트 루프
- 한투: `requests` 라이브러리 기반 동기식 HTTP 호출

**통합 가능성**:
```python
# 키움 이벤트 핸들러 내에서 한투 REST 호출 가능
def _receive_real_condition(self, strCode, strType, strConditionName, strConditionIndex):
    if strType == "I":  # 편입
        # 한투 REST API로 즉시 주문 (동기식 - 블로킹 최소화 필요)
        self.kis_agent.do_buy(strCode, qty=10, price=0, order_type="03")
```

**주의사항**:
- 한투 REST 호출은 동기식 → 키움 이벤트 루프 블로킹 발생 가능
- **해결책**: `QThread` 또는 `threading.Thread`로 한투 주문을 별도 스레드에서 처리

### ✅ 검증 완료: 32비트 Python 환경 호환성

| 라이브러리 | 32비트 호환 | 비고 |
|-----------|-------------|------|
| PyQt5 | ✅ | `pip install PyQt5` |
| requests | ✅ | 순수 Python |
| pandas | ✅ | `pip install pandas` |
| loguru | ✅ | 순수 Python |
| pycryptodome | ✅ | 한투 웹소켓 복호화용 (선택) |

### ⚠️ 주의: 한투 웹소켓 사용 시 복잡도 증가

**한투 웹소켓 (실시간 체결통보)을 사용하려면**:
- `asyncio` + `websockets` 필요
- PyQt5 이벤트 루프와 충돌 가능성
- **권장**: Phase 1에서는 REST polling 방식으로 시작, 웹소켓은 Phase 2에서 추가

---

# 3. 시스템 아키텍처

## 3.1 전체 구조도

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        K-Hunter Main Process                            │
│                        (32-bit Python 3.10)                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌─────────────────┐                                                   │
│   │   MainWindow    │  ← PyQt5 QMainWindow (이벤트 루프)                │
│   │   (Controller)  │                                                   │
│   └────────┬────────┘                                                   │
│            │                                                            │
│   ┌────────┴────────┬──────────────────┬──────────────────┐            │
│   │                 │                  │                  │            │
│   ▼                 ▼                  ▼                  ▼            │
│ ┌─────────┐   ┌──────────┐   ┌──────────────┐   ┌─────────────┐       │
│ │ Kiwoom  │   │ Strategy │   │   KIS Agent  │   │  Messenger  │       │
│ │ Agent   │   │  Agent   │   │   (Trader)   │   │ (Telegram)  │       │
│ │ (Scout) │   │ (Brain)  │   │              │   │             │       │
│ └────┬────┘   └────┬─────┘   └──────┬───────┘   └──────┬──────┘       │
│      │             │                │                  │              │
│      │   Signal    │   Signal       │   REST API       │   HTTP       │
│      │   (PyQt)    │   Queue        │                  │              │
│      ▼             ▼                ▼                  ▼              │
│ ┌─────────┐   ┌──────────┐   ┌──────────────┐   ┌─────────────┐       │
│ │ 키움    │   │  Python  │   │   한투증권   │   │  Telegram   │       │
│ │ 서버    │   │  Queue   │   │   서버       │   │   서버      │       │
│ └─────────┘   └──────────┘   └──────────────┘   └─────────────┘       │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## 3.2 에이전트 상세 설계

### 3.2.1 Kiwoom Agent (Scout) - 정찰병

**역할**: 키움증권 OpenAPI 제어, 실시간 조건검색 감시

**핵심 클래스**: `KiwoomAgent`
```python
class KiwoomAgent(QObject):
    # PyQt5 시그널 정의
    condition_matched = pyqtSignal(str, str, str)  # 종목코드, 편입/이탈, 조건명

    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self._connect_slots()

    # 핵심 메서드
    def login(self) -> bool
    def get_condition_list(self) -> dict[str, int]  # {조건명: 인덱스}
    def start_realtime_condition(self, condition_name: str)
    def stop_realtime_condition(self, condition_name: str)

    # 이벤트 핸들러 (슬롯)
    def _on_receive_real_condition(self, code, type, cond_name, cond_idx)
```

**키움 API 함수 매핑**:
| 함수 | 용도 |
|------|------|
| `CommConnect()` | 로그인 창 열기 |
| `GetConditionLoad()` | 조건식 리스트 서버에서 가져오기 |
| `SendCondition(scrNo, condName, condIdx, nSearch)` | 조건검색 실행 (nSearch=1: 실시간) |
| `SendConditionStop(scrNo, condName, condIdx)` | 실시간 조건검색 중지 |

**이벤트 슬롯 매핑**:
| 이벤트 | 용도 |
|--------|------|
| `OnEventConnect` | 로그인 결과 |
| `OnReceiveConditionVer` | 조건식 리스트 수신 |
| `OnReceiveTrCondition` | 조건검색 결과 (일회성) |
| `OnReceiveRealCondition` | **실시간 조건 편입/이탈** |

---

### 3.2.2 KIS Agent (Trader) - 트레이더

**역할**: 한국투자증권 REST API 통신, 주문 실행

**핵심 클래스**: `KISAgent`
```python
class KISAgent:
    def __init__(self, config: dict):
        self.base_url = config['url']  # 실전 or 모의
        self.app_key = config['app_key']
        self.app_secret = config['app_secret']
        self.account_num = config['account_num']
        self.access_token = None
        self.token_expires_at = None

    # 인증
    def get_access_token(self) -> str
    def refresh_token_if_needed(self)

    # 시세 조회
    def get_current_price(self, stock_code: str) -> dict
    def get_balance(self) -> tuple[int, pd.DataFrame]  # (총평가, 잔고DF)

    # 주문
    def buy(self, stock_code: str, qty: int, price: int, order_type: str) -> dict
    def sell(self, stock_code: str, qty: int, price: int, order_type: str) -> dict
    def cancel(self, order_no: str) -> dict
```

**한투 API 엔드포인트 매핑**:
| 기능 | 엔드포인트 | TR_ID |
|------|-----------|-------|
| 토큰 발급 | POST `/oauth2/tokenP` | - |
| 현재가 조회 | GET `/uapi/domestic-stock/v1/quotations/inquire-price` | FHKST01010100 |
| 잔고 조회 | GET `/uapi/domestic-stock/v1/trading/inquire-balance` | TTTC8434R |
| 현금 매수 | POST `/uapi/domestic-stock/v1/trading/order-cash` | TTTC0802U |
| 현금 매도 | POST `/uapi/domestic-stock/v1/trading/order-cash` | TTTC0801U |

**주문 유형 코드**:
| 코드 | 의미 |
|------|------|
| `00` | 지정가 |
| `01` | 시장가 |
| `03` | 시장가 (키움 호환) |

---

### 3.2.3 Strategy Agent (Brain) - 전략가

**역할**: 신호 필터링, 조건 조합, 자금 관리

**핵심 클래스**: `StrategyAgent`
```python
class StrategyAgent:
    def __init__(self, config: dict):
        self.signal_queue = Queue()  # 키움에서 받은 신호 저장
        self.holdings = set()  # 현재 보유 종목
        self.blacklist = set()  # 매매 제외 종목
        self.condition_sets = {
            'RSI과매도': set(),
            '거래량급증': set(),
        }

        # 자금 관리 설정
        self.max_holdings = config.get('max_holdings', 10)
        self.per_stock_amount = config.get('per_stock_amount', 1_000_000)

    # 필터링
    def is_valid_signal(self, stock_code: str) -> bool
    def check_combined_condition(self, stock_code: str) -> bool

    # 자금 관리
    def calculate_order_qty(self, stock_code: str, current_price: int) -> int

    # 신호 처리
    def process_signal(self, stock_code: str, condition_name: str, signal_type: str)
```

**필터링 규칙**:
```python
def is_valid_signal(self, stock_code: str) -> bool:
    # 1. 중복 매수 방지
    if stock_code in self.holdings:
        return False

    # 2. 블랙리스트 체크
    if stock_code in self.blacklist:
        return False

    # 3. 최대 보유 종목 수 체크
    if len(self.holdings) >= self.max_holdings:
        return False

    # 4. 관리종목/거래정지 체크 (키움에서 조회)
    # ...

    return True
```

**조건 조합 전략**:
```python
def check_combined_condition(self, stock_code: str) -> bool:
    """
    전략 예시: RSI 과매도 AND 거래량 급증 동시 만족
    """
    is_rsi_oversold = stock_code in self.condition_sets['RSI과매도']
    is_volume_surge = stock_code in self.condition_sets['거래량급증']

    # AND 조건
    return is_rsi_oversold and is_volume_surge
```

---

### 3.2.4 Messenger Agent - 통신병

**역할**: 사용자 알림 (텔레그램)

**핵심 클래스**: `MessengerAgent`
```python
class MessengerAgent:
    def __init__(self, token: str, chat_id: str):
        self.bot = telegram.Bot(token=token)
        self.chat_id = chat_id
        self.message_queue = deque(maxlen=100)

    def send_message(self, text: str)
    def send_buy_notification(self, stock_code: str, qty: int, price: int)
    def send_sell_notification(self, stock_code: str, qty: int, price: int, profit: float)
    def send_error_notification(self, error_msg: str)
```

---

## 3.3 데이터 흐름 상세

### 3.3.1 매수 플로우

```
[키움 서버]
    │
    ▼ OnReceiveRealCondition(code="005930", type="I", cond="RSI과매도")
┌───────────────┐
│ Kiwoom Agent  │
│               │
│ emit signal   │──────────────────────────────┐
└───────────────┘                              │
                                               ▼
                                    ┌───────────────────┐
                                    │  Strategy Agent   │
                                    │                   │
                                    │  1. 필터링        │
                                    │  2. 조건 조합     │
                                    │  3. 자금 계산     │
                                    └─────────┬─────────┘
                                              │
                                              ▼ (필터 통과 시)
                                    ┌───────────────────┐
                                    │    KIS Agent      │
                                    │                   │
                                    │  1. 현재가 조회   │
                                    │  2. 매수 주문     │
                                    └─────────┬─────────┘
                                              │
                                              ▼
                                    ┌───────────────────┐
                                    │   Messenger       │
                                    │                   │
                                    │  "005930 매수!"   │
                                    └───────────────────┘
```

### 3.3.2 매도 플로우 (익절/손절)

```
[QTimer - 1초마다 실행]
    │
    ▼
┌───────────────────┐
│  MainController   │
│                   │
│  check_holdings() │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐      ┌───────────────────┐
│    KIS Agent      │      │  Strategy Agent   │
│                   │      │                   │
│  get_balance()    │─────▶│  check_exit()     │
│                   │      │  - 익절: +3%      │
│                   │      │  - 손절: -2%      │
└───────────────────┘      └─────────┬─────────┘
                                     │
                                     ▼ (청산 조건 만족 시)
                           ┌───────────────────┐
                           │    KIS Agent      │
                           │                   │
                           │  sell()           │
                           └───────────────────┘
```

---

# 4. 트레이딩 로직 설계

## 4.1 진입 전략 (Entry)

### 4.1.1 조건검색 설정 가이드

**HTS (영웅문)에서 설정할 조건식 예시**:

| 조건명 | 설정 내용 | 용도 |
|--------|----------|------|
| RSI과매도 | RSI(14) ≤ 30 | 과매도 구간 진입 종목 |
| 거래량급증 | 당일거래량 ≥ 전일거래량 × 2 | 거래량 폭발 종목 |
| 이격도하락 | 이격도(20) ≤ 95 | 이동평균선 대비 하락 종목 |

### 4.1.2 Python 조합 로직

```python
# 전략 1: 단순 AND
if 'RSI과매도' in conditions and '거래량급증' in conditions:
    return True

# 전략 2: 시간차 AND (RSI 먼저, 거래량 나중)
if stock_code in self.rsi_triggered:
    if time.time() - self.rsi_triggered[stock_code] < 180:  # 3분 이내
        if '거래량급증' in conditions:
            return True

# 전략 3: OR (둘 중 하나)
if 'RSI과매도' in conditions or '이격도하락' in conditions:
    return True
```

## 4.2 청산 전략 (Exit)

### 4.2.1 익절 (Take Profit)
```python
target_profit_rate = 3.0  # 3%

if current_profit_rate >= target_profit_rate:
    # 시장가 매도
    self.kis_agent.sell(stock_code, qty, price=0, order_type="01")
```

### 4.2.2 손절 (Stop Loss)
```python
stop_loss_rate = -2.0  # -2%

if current_profit_rate <= stop_loss_rate:
    # 시장가 매도
    self.kis_agent.sell(stock_code, qty, price=0, order_type="01")
```

### 4.2.3 트레일링 스탑 (선택)
```python
class TrailingStop:
    def __init__(self, trigger_rate=2.0, trail_rate=1.0):
        self.trigger_rate = trigger_rate  # 트레일링 시작 조건
        self.trail_rate = trail_rate      # 고점 대비 하락 허용폭
        self.high_water_mark = {}         # 종목별 최고 수익률 기록

    def check(self, stock_code: str, current_rate: float) -> bool:
        if current_rate >= self.trigger_rate:
            if stock_code not in self.high_water_mark:
                self.high_water_mark[stock_code] = current_rate
            else:
                self.high_water_mark[stock_code] = max(
                    self.high_water_mark[stock_code],
                    current_rate
                )

            # 고점 대비 하락 시 매도
            if current_rate <= self.high_water_mark[stock_code] - self.trail_rate:
                return True

        return False
```

## 4.3 자금 관리 (Money Management)

### 4.3.1 고정 금액 방식
```python
class FixedAmountManager:
    def __init__(self, amount_per_stock: int = 1_000_000):
        self.amount_per_stock = amount_per_stock

    def calculate_qty(self, current_price: int) -> int:
        return self.amount_per_stock // current_price
```

### 4.3.2 비율 방식
```python
class PercentageManager:
    def __init__(self, pct_per_stock: float = 10.0, max_holdings: int = 10):
        self.pct_per_stock = pct_per_stock
        self.max_holdings = max_holdings

    def calculate_qty(self, total_balance: int, current_price: int) -> int:
        available = total_balance * (self.pct_per_stock / 100)
        return int(available // current_price)
```

### 4.3.3 피라미딩 (분할 매수)
```python
class PyramidManager:
    def __init__(self, initial_pct: float = 50.0, additional_pct: float = 50.0):
        self.initial_pct = initial_pct
        self.additional_pct = additional_pct
        self.buy_count = {}  # 종목별 매수 횟수

    def calculate_qty(self, stock_code: str, total_amount: int, current_price: int) -> int:
        count = self.buy_count.get(stock_code, 0)

        if count == 0:
            # 1차 매수: 50%
            amount = total_amount * (self.initial_pct / 100)
        elif count == 1:
            # 2차 매수 (물타기): 나머지 50%
            amount = total_amount * (self.additional_pct / 100)
        else:
            return 0

        self.buy_count[stock_code] = count + 1
        return int(amount // current_price)
```

---

# 5. 동시성 및 안정성 설계

## 5.1 스레드 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                     Main Thread (PyQt5)                     │
│                                                             │
│  - QApplication 이벤트 루프                                 │
│  - Kiwoom OCX 이벤트 수신                                   │
│  - UI 업데이트                                              │
│  - QTimer 기반 주기적 작업                                   │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Order Worker   │ │ Balance Worker  │ │ Telegram Worker │
│    Thread       │ │    Thread       │ │    Thread       │
│                 │ │                 │ │                 │
│ - 한투 매수/매도│ │ - 잔고 조회     │ │ - 메시지 전송   │
│ - Queue에서     │ │ - 1초 주기      │ │ - Queue에서     │
│   주문 꺼내기   │ │                 │ │   메시지 꺼내기 │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## 5.2 Queue 기반 비동기 처리

```python
from queue import Queue
from threading import Thread

class OrderWorker(Thread):
    def __init__(self, order_queue: Queue, kis_agent: KISAgent):
        super().__init__(daemon=True)
        self.order_queue = order_queue
        self.kis_agent = kis_agent
        self.running = True

    def run(self):
        while self.running:
            try:
                order = self.order_queue.get(timeout=1)
                if order is None:
                    continue

                order_type, stock_code, qty, price = order

                if order_type == 'BUY':
                    result = self.kis_agent.buy(stock_code, qty, price)
                elif order_type == 'SELL':
                    result = self.kis_agent.sell(stock_code, qty, price)

                # Rate Limit 준수 (초당 4회)
                time.sleep(0.25)

            except Empty:
                continue
            except Exception as e:
                logger.exception(e)
```

## 5.3 Rate Limit 관리

### 키움증권 제한
```python
class KiwoomRateLimiter:
    def __init__(self):
        self.max_per_second = 4
        self.max_per_hour = 990
        self.request_times = deque(maxlen=1000)

    def can_request(self) -> bool:
        now = datetime.now()

        # 최근 1초 내 요청 수
        recent_1s = sum(1 for t in self.request_times
                       if (now - t).total_seconds() < 1)
        if recent_1s >= self.max_per_second:
            return False

        # 최근 1시간 내 요청 수
        recent_1h = sum(1 for t in self.request_times
                       if (now - t).total_seconds() < 3600)
        if recent_1h >= self.max_per_hour:
            return False

        return True

    def record_request(self):
        self.request_times.append(datetime.now())
```

### 한투증권 제한
```python
class KISRateLimiter:
    def __init__(self):
        self.min_interval = 0.2  # 초당 최대 5회
        self.last_request_time = 0

    def wait_if_needed(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()
```

## 5.4 장애 복구 전략

### 5.4.1 연결 끊김 감지 및 재연결
```python
class ConnectionMonitor:
    def __init__(self, kiwoom_agent, kis_agent):
        self.kiwoom = kiwoom_agent
        self.kis = kis_agent
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_connections)
        self.timer.start(10000)  # 10초마다 체크

    def check_connections(self):
        # 키움 연결 상태 확인
        if self.kiwoom.kiwoom.dynamicCall("GetConnectState()") == 0:
            logger.error("키움 연결 끊김!")
            self.notify_user("키움 연결이 끊겼습니다. 재시작 필요.")

        # 한투 토큰 유효성 확인
        try:
            self.kis.refresh_token_if_needed()
        except Exception as e:
            logger.error(f"한투 토큰 갱신 실패: {e}")
```

### 5.4.2 미체결 주문 관리
```python
class UnfilledOrderManager:
    def __init__(self, timeout_seconds: int = 60):
        self.timeout = timeout_seconds
        self.unfilled_orders = {}  # {order_no: (stock_code, order_time, qty)}

    def add_order(self, order_no: str, stock_code: str, qty: int):
        self.unfilled_orders[order_no] = (stock_code, datetime.now(), qty)

    def check_timeout(self, kis_agent: KISAgent):
        now = datetime.now()
        for order_no, (stock_code, order_time, qty) in list(self.unfilled_orders.items()):
            if (now - order_time).seconds >= self.timeout:
                logger.info(f"미체결 주문 타임아웃: {order_no}")
                # 취소 후 시장가 정정
                kis_agent.cancel(order_no)
                # 또는 시장가로 재주문
                # kis_agent.buy(stock_code, qty, price=0, order_type="01")
```

---

# 6. 프로젝트 구조

## 6.1 디렉토리 구조

```
k-hunter/
├── config/
│   ├── config.yaml           # 설정 파일
│   └── config.example.yaml   # 설정 예시
├── src/
│   ├── __init__.py
│   ├── main.py               # 진입점
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── kiwoom_agent.py   # 키움 에이전트
│   │   ├── kis_agent.py      # 한투 에이전트
│   │   ├── strategy_agent.py # 전략 에이전트
│   │   └── messenger.py      # 텔레그램 에이전트
│   ├── core/
│   │   ├── __init__.py
│   │   ├── controller.py     # 메인 컨트롤러
│   │   ├── rate_limiter.py   # API 제한 관리
│   │   └── order_queue.py    # 주문 큐 관리
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── entry.py          # 진입 전략
│   │   ├── exit.py           # 청산 전략
│   │   └── money_mgmt.py     # 자금 관리
│   └── utils/
│       ├── __init__.py
│       ├── logger.py         # 로깅 설정
│       └── helpers.py        # 유틸리티 함수
├── ui/
│   └── main_window.ui        # PyQt5 UI 파일
├── logs/
│   └── .gitkeep
├── data/
│   └── .gitkeep
├── tests/
│   └── __init__.py
├── requirements.txt
└── README.md
```

## 6.2 설정 파일 구조

```yaml
# config/config.yaml

# 모드 설정
is_paper_trading: true  # true: 모의투자, false: 실전

# 키움증권 설정 (OCX 방식이므로 API Key 불필요)
kiwoom:
  conditions:
    - name: "RSI과매도"
      realtime: true
    - name: "거래량급증"
      realtime: true

# 한국투자증권 설정
kis:
  # 실전
  url: "https://openapi.koreainvestment.com:9443"
  app_key: "YOUR_APP_KEY"
  app_secret: "YOUR_APP_SECRET"
  account_num: "12345678-01"

  # 모의
  paper_url: "https://openapivts.koreainvestment.com:29443"
  paper_app_key: "YOUR_PAPER_APP_KEY"
  paper_app_secret: "YOUR_PAPER_APP_SECRET"
  paper_account_num: "98765432-01"

# 전략 설정
strategy:
  # 진입
  entry:
    combine_mode: "AND"  # AND, OR, SEQUENTIAL
    max_holdings: 10
    per_stock_amount: 1000000

  # 청산
  exit:
    take_profit: 3.0      # 익절 %
    stop_loss: -2.0       # 손절 %
    trailing_stop:
      enabled: false
      trigger: 2.0
      trail: 1.0

# 텔레그램 설정
telegram:
  enabled: true
  token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"

# 운영 시간
schedule:
  start_time: "09:00"
  end_time: "15:20"
  auto_shutdown: "15:30"
```

---

# 7. 개발 로드맵

## 7.1 Phase 0: 환경 구축 (1-2일)

### 체크리스트
- [ ] Windows PC 준비
- [ ] Anaconda 설치
- [ ] 32비트 Python 가상환경 생성
  ```bash
  set CONDA_FORCE_32BIT=1
  conda create -n khunter python=3.10
  conda activate khunter
  ```
- [ ] 키움 OpenAPI+ 설치 및 계정 등록
- [ ] 한투 KIS Developers 가입 및 API Key 발급
- [ ] 의존성 설치
  ```bash
  pip install pyqt5 requests pandas loguru pyyaml python-telegram-bot
  ```

## 7.2 Phase 1: KIS Agent (2-3일)

### 목표
- 한투 API로 토큰 발행, 잔고 조회, 주문 테스트

### 구현 순서
1. `KoreaInvestEnv` 클래스 (토큰 발행)
2. `KISAgent.get_current_price()` (시세 조회)
3. `KISAgent.get_balance()` (잔고 조회)
4. `KISAgent.buy()` / `KISAgent.sell()` (주문)
5. **모의투자 환경에서 삼성전자 1주 매수/매도 테스트**

### 검증 기준
```python
# 테스트 코드
agent = KISAgent(config)
print(agent.get_current_price("005930"))  # 삼성전자 현재가
print(agent.get_balance())  # 잔고
result = agent.buy("005930", qty=1, price=0, order_type="01")  # 시장가 1주
print(result)
```

## 7.3 Phase 2: Kiwoom Agent (3-4일)

### 목표
- 키움 로그인, 조건검색 리스트 로드, 실시간 감시 테스트

### 구현 순서
1. `KiwoomAgent.__init__()` (QAxWidget 초기화)
2. `KiwoomAgent.login()` (로그인 창)
3. `KiwoomAgent.get_condition_list()` (조건식 목록)
4. `KiwoomAgent.start_realtime_condition()` (실시간 감시)
5. **장중에 조건 만족 종목 콘솔 출력 테스트**

### 검증 기준
```
[14:30:15] OnReceiveRealCondition: 005930, I, RSI과매도
[14:30:18] OnReceiveRealCondition: 035720, I, RSI과매도
[14:32:45] OnReceiveRealCondition: 005930, D, RSI과매도  (이탈)
```

## 7.4 Phase 3: Strategy Agent (2-3일)

### 목표
- 필터링 로직, 조건 조합, 자금 관리 구현

### 구현 순서
1. `StrategyAgent.is_valid_signal()` (필터링)
2. `StrategyAgent.check_combined_condition()` (조합)
3. `StrategyAgent.calculate_order_qty()` (자금 계산)
4. 주문 Queue 연동
5. **키움 신호 → 필터링 → 콘솔 출력 테스트**

## 7.5 Phase 4: 통합 (3-4일)

### 목표
- 전체 데이터 플로우 연결, 실제 자동매매 1사이클

### 구현 순서
1. MainController 구현 (Signal/Slot 연결)
2. OrderWorker 스레드 구현
3. 익절/손절 로직 연동
4. **모의투자 환경에서 자동매매 테스트**

### 검증 시나리오
```
1. 프로그램 시작 → 키움/한투 로그인
2. 조건검색 "RSI과매도" 실시간 감시 시작
3. 종목 편입 신호 수신
4. 필터링 통과 → 한투로 시장가 매수
5. 잔고 모니터링 → +3% 도달 시 시장가 매도
6. 텔레그램 알림 수신
```

## 7.6 Phase 5: Messenger + 고도화 (2-3일)

### 구현 항목
- 텔레그램 봇 연동
- 미체결 주문 관리
- 로깅 강화
- 설정 저장/로드
- UI 개선 (선택)

---

# 8. 리스크 및 주의사항

## 8.1 기술적 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 키움 서버 점검 (07:30-08:00) | 조건검색 불가 | 장 시작 전 재로그인 |
| 한투 토큰 만료 (24시간) | 주문 실패 | 자동 갱신 로직 |
| 인터넷 끊김 | 전체 중단 | 연결 모니터링, 알림 |
| PC 절전/종료 | 전체 중단 | 절전 해제, UPS |

## 8.2 트레이딩 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 급등/급락장 | 손절 미체결, 슬리피지 | 시장가 주문, 타임아웃 정정 |
| 과매도 함정 | 추가 하락 | 손절 엄격 적용, 분할 매수 |
| 조건식 과최적화 | 실전 성과 저조 | 백테스트, 소액 테스트 |

## 8.3 법적 고려사항

- **실전 투자 전 충분한 모의 테스트 필수**
- **자동매매로 인한 손실은 본인 책임**
- **세금 신고 (양도소득세) 고려**

---

# 9. 참고 자료

## 9.1 프로젝트 내 참고 코드

| 파일 | 용도 |
|------|------|
| `StockCodingLeture-main/KiwoomOPENAPI/chapter6/common_api.py` | 키움 API 종합 예제 |
| `StockCodingLeture-main/KiwoomOPENAPI/chapter6/trading_product.py` | 키움 자동매매 예제 |
| `StockCodingLeture-main/KRInvestTradingSystem/chapter4/utils.py` | 한투 API 유틸리티 |
| `StockCodingLeture-main/KRInvestTradingSystem/chapter4/example4-1.py` | 한투 웹소켓 예제 |

## 9.2 공식 문서

- 키움 OpenAPI+: https://www.kiwoom.com/h/customer/download/VOpenApiInfoView
- 한투 KIS Developers: https://apiportal.koreainvestment.com/

---

# 10. 부록: AI 개발 요청 프롬프트

이 설계서를 기반으로 Claude/GPT에게 코드 생성을 요청할 때 사용할 프롬프트:

```
나는 K-Hunter 프로젝트의 기술 설계서를 가지고 있어.

지금부터 Phase [N] 의 [클래스명] 을 구현해줘.

요구사항:
1. 설계서의 시그니처를 따를 것
2. 타입 힌트 사용
3. 에러 핸들링 포함
4. loguru로 로깅

참고할 기존 코드:
[참고 코드 내용]

구현해야 할 클래스/함수:
[설계서에서 해당 부분 복사]
```

---

**문서 끝**
