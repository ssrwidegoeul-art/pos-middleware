r"""
POS Middleware - 돈까스/마제소바 매장 전용
배달 주문 시 부자재 목록을 자동으로 추가 출력

[동작 방식]
1. config.json의 VID/PID로 SAM4S USB 프린터를 찾아 연결합니다.
   프린터가 연결되지 않으면 오류 메시지를 출력하고 종료하지 않은 채 대기 상태를 유지합니다.
2. 프린터로 전송되는 ESC/POS 데이터를 실시간으로 수신하여 텍스트로 변환합니다.
3. 영수증 헤더에 '배달'/'한점배달'/'알콜' 키워드가 있으면 배달 주문으로 인식합니다.
   - 배달 주문 + '[매장용]'  → 부자재 목록을 영수증 하단에 추가
   - 홀 주문 또는 '[고객용]' → 원본 그대로 유지
4. 처리 내역(시간, 수신된 데이터, 처리 결과, 오류 내용)은 log.txt에 기록합니다.
   처리된 영수증은 output/ 폴더에 저장됩니다.

[실행 방법]
  python main.py          # 정상 실행 (USB 데이터 수신 대기)
  python main.py --test   # 내장 테스트 실행

[Windows 단일 exe 빌드]
  build.bat 실행 → dist 폴더에 POSMiddleware.exe 생성 (config.json은 exe와 같은 폴더에 위치해야 함)

[참고]
- Windows에서 pyusb로 프린터에 접근하려면 libusb 드라이버(Zadig 등) 설치가 필요할 수 있습니다.
- 프린터 VID/PID 확인: Windows 장치 관리자 → 해당 USB 장치 → 속성 → 자세히 → 하드웨어 ID
  (예: USB\VID_1FC9&PID_2018 → config.json에 "vid": "0x1FC9", "pid": "0x2018")
- 한글 영수증은 CP949(KSC5601) 인코딩을 가정합니다. 다른 인코딩이면 RECEIPT_ENCODING을 수정하세요.
"""

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime

# USB 라이브러리는 선택 의존성입니다. 없어도 --test 모드와 로그 기능은 동작합니다.
try:
    import usb.core
    import usb.util

    USB_AVAILABLE = True
except ImportError:
    USB_AVAILABLE = False


# ===== 경로 / 상수 =====
def app_dir():
    """실행 파일(또는 소스)이 위치한 폴더. PyInstaller 단일 exe에서도 동작합니다."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")
LOG_PATH = os.path.join(app_dir(), "log.txt")
OUTPUT_DIR = os.path.join(app_dir(), "output")

RECEIPT_ENCODING = "cp949"  # 한글 ESC/POS 영수증 인코딩 (KSC5601/CP949)

# 홀/배달 구분 키워드 (요청사항 3)
DELIVERY_KEYWORDS = ("배달", "한점배달", "알콜")
STORE_MARK = "[매장용]"
CUSTOMER_MARK = "[고객용]"

# USB 수신 관련
USB_POLL_INTERVAL_SEC = 5      # 프린터 미연결 시 재시도 간격(초)
USB_READ_TIMEOUT_MS = 1000     # 단일 read 타임아웃(ms)
RECEIPT_IDLE_TIMEOUT_SEC = 3   # 데이터 유입 정지 후 영수증 확정까지 대기(초)
MAX_RECEIPT_BYTES = 64 * 1024  # 영수증 수신 버퍼 상한(바이트)

_CUT_PREFIX = b"\x1d\x56"      # GS V — 용지 커터 명령(영수증 끝 신호)


# ===== 매핑 데이터 =====
MENU_MAPPING = {
    "마제소바 (미니공기밥포함)": {"미니밥": 1, "장국": 1, "반찬2종": 1},
    "매운마제소바 (미니공기밥포함)": {"미니밥": 1, "장국": 1, "반찬2종": 1},
    "마라소바": {"미니밥": 1, "장국": 1, "반찬2종": 1},
    "키마카레": {"장국": 1, "반찬2종": 1},
    "크림키마카레": {"장국": 1, "반찬2종": 1},
    "오를렛키마카레": {"장국": 1, "반찬2종": 1},
    "돈까스 카레": {"장국": 1, "반찬2종": 1},
    "카레우동": {"장국": 1, "반찬2종": 1},
    "더블에그카레": {"장국": 1, "반찬2종": 1},
    "가츠동": {"장국": 1, "반찬2종": 1},
    "가라이게동": {"장국": 1, "반찬2종": 1},
    "더블 등심돈까스": {"장국": 1, "소스2종": 1},
    "안심돈까스": {"장국": 1, "소스2종": 1},
    "매콤파향 돈까스": {"장국": 1, "매콤소스": 1},
    "청양마요 돈까스": {"장국": 1, "마요소스": 1},
    "경양식 돈까스": {"장국": 1, "경양식소스": 1},
    "치즈를까스": {"장국": 1, "소스2종": 1},
    "사각치즈돈까스": {"장국": 1, "소스2종": 1},
    "생선까스": {"장국": 1, "타르타르소스(대)": 1},
    "등심반 안심반": {"장국": 1, "소스2종": 1},
    "안심반 치즈를반": {"장국": 1, "소스2종": 1},
    "치킨가라아게 카레": {"장국": 1, "반찬2종": 1},
    "옛날 어묵 우동": {"반찬2종": 1},
    "김치 어묵 우동": {"반찬2종": 1},
    "김치나베 (미니공기밥포함)": {"미니밥": 1, "반찬2종": 1},
    "김치가츠나베 (미니공기밥포함)": {"미니밥": 1, "반찬2종": 1},
    "생면 냉모밀": {"간무와사비": 1, "반찬2종": 1},
    "새콤 생면 비빔모밀": {"장국": 1, "반찬2종": 1},
    "들기름 소바": {"장국": 1, "반찬2종": 1},
    "참기름 비빔소바": {"장국": 1, "반찬2종": 1},
    "쫄면": {"장국": 1, "반찬2종": 1},
}

# ===== 옵션 메뉴 매핑 =====
OPTION_MAPPING = {
    "새우튀김 1P": {"타르타르소스(소)": 1},
    "(수제) 등심돈까스 1p": {"1구소스": 1},
    "(수제) 안심돈까스 1p": {"1구소스": 1},
    "(수제) 치즈돈까스 1p": {"1구소스": 1},
    "(통통) 생선까스 1p": {"타르타르소스(소)": 1},
    "미니냉모밀": {"간무와사비": 1},
}


# ===== 설정 파일 (요청사항 4) =====
DEFAULT_CONFIG = {
    "printer": {"vid": "0x0000", "pid": "0x0000"},
    "debug": False,
}


def load_config(path=CONFIG_PATH):
    """config.json을 읽어 설정을 반환합니다. 파일이 없거나 손상되면 기본값을 사용합니다."""
    config = {
        "printer": dict(DEFAULT_CONFIG["printer"]),
        "debug": DEFAULT_CONFIG["debug"],
    }
    if not os.path.exists(path):
        print(f"[경고] {path} 파일이 없습니다. 기본 설정으로 실행합니다.")
        return config
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config["printer"].update(user_config.get("printer", {}))
        config["debug"] = bool(user_config.get("debug", config["debug"]))
    except (OSError, ValueError) as e:
        print(f"[경고] config.json 로드 실패 ({e}). 기본 설정으로 실행합니다.")
    return config


def parse_hex(value):
    """'0x1FC9' 형태의 문자열(또는 정수)을 정수로 변환합니다."""
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 0)


# ===== 로그 기능 (요청사항 6) =====
def log_message(message, level="INFO"):
    """log.txt에 [시간] [레벨] 메시지를 기록합니다."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 로그 기록 실패가 동작을 중단시키지 않도록 함
    return line


def log_error(message):
    return log_message(message, "ERROR")


def log_debug(config, message):
    if config.get("debug"):
        return log_message(message, "DEBUG")
    return None


def debug_print(config, message):
    if config.get("debug"):
        print(f"[DEBUG] {message}")


# ===== ESC/POS → 텍스트 변환 (요청사항 2) =====
# ESC 명령: 코드 → 파라미터 바이트 수
_ESC_PARAMS = {
    0x21: 1,  # ESC ! n   (인쇄 모드)
    0x25: 0,  # ESC %     (문자 선택)
    0x2D: 1,  # ESC - n   (밑줄)
    0x33: 1,  # ESC 3 n   (줄 간격)
    0x3D: 1,  # ESC = n   (주변 장치 선택)
    0x40: 0,  # ESC @     (초기화)
    0x45: 1,  # ESC E n   (강조)
    0x47: 1,  # ESC G n   (이중 인쇄)
    0x4A: 1,  # ESC J n   (용지 이송)
    0x4D: 1,  # ESC M n   (문자 폰트)
    0x52: 1,  # ESC R n   (국제 문자)
    0x53: 0,  # ESC S     (표준 모드)
    0x54: 1,  # ESC T n   (인쇄 방향)
    0x56: 1,  # ESC V n   (90도 회전)
    0x57: 8,  # ESC W ... (인쇄 영역)
    0x61: 1,  # ESC a n   (정렬)
    0x63: 2,  # ESC c 5 n (패널 버튼)
    0x64: 1,  # ESC d n   (n줄 용지 이송/커트)
    0x69: 0,  # ESC i     (전체 컷)
    0x70: 2,  # ESC p m t1 t2 (펄스)
    0x72: 1,  # ESC r n   (인쇄 색상)
    0x74: 1,  # ESC t n   (코드 페이지)
    0x75: 1,  # ESC u n   (시프트)
    0x76: 1,  # ESC v n   (전송 상태)
    0x7B: 1,  # ESC { n   (상하 반전)
}

# GS 명령: 코드 → 파라미터 바이트 수
_GS_PARAMS = {
    0x21: 1,  # GS ! n   (문자 크기)
    0x42: 2,  # GS B n   (반전)
    0x48: 3,  # GS H n   (HRI 문자)
    0x4C: 2,  # GS L nL nH (왼쪽 여백)
    0x56: 1,  # GS V m   (용지 커터)
    0x57: 2,  # GS W nL nH (인쇄 폭)
    0x66: 2,  # GS f n   (폰트)
    0x68: 2,  # GS h n   (바코드 높이)
    0x72: 1,  # GS r n   (전송 상태)
    0x77: 4,  # GS w n   (바코드 폭)
    0x78: 1,  # GS x n   (코드 페이지, 일부 기종)
}


def _command_length(data, start, param_table):
    """start(ESC/GS 위치)부터 시작하는 명령어의 전체 길이를 계산합니다."""
    if start + 1 >= len(data):
        return len(data) - start
    cmd = data[start + 1]
    if cmd == 0x28:  # GS ( L pL pH ... 형태의 가변 길이 명령
        pl = data[start + 3] if start + 3 < len(data) else 0
        ph = data[start + 4] if start + 4 < len(data) else 0
        return 5 + pl + ph * 256
    params = param_table.get(cmd)
    if params is None:
        return 2  # 알 수 없는 명령은 최소 2바이트(ESC/GS + 코드)만 건너뜀
    return 2 + params


def escpos_data_to_text(data):
    """ESC/POS 바이트 데이터를 읽을 수 있는 텍스트로 변환합니다.

    ESC/GS 제어 명령과 제어 문자를 제거하고, 줄바꿈(LF)을 유지한 뒤
    RECEIPT_ENCODING(cp949)으로 디코딩합니다.
    """
    if not data:
        return ""
    if isinstance(data, str):
        return data

    cleaned = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b == 0x1B and i + 1 < n:  # ESC 명령 제거
            i += _command_length(data, i, _ESC_PARAMS)
            continue
        if b == 0x1D and i + 1 < n:  # GS 명령 제거
            i += _command_length(data, i, _GS_PARAMS)
            continue
        if b == 0x00:  # 널 문자 제거
            i += 1
            continue
        if b == 0x0A:  # LF → 줄바꿈 유지
            cleaned.append(b)
            i += 1
            continue
        if b == 0x0D:  # CR 무시 (LF가 줄바꿈 담당)
            i += 1
            continue
        if b < 0x20:  # 기타 제어 문자 제거
            i += 1
            continue
        cleaned.append(b)
        i += 1

    return cleaned.decode(RECEIPT_ENCODING, errors="replace").strip()


# ===== 영수증 처리 (홀/배달 구분, 요청사항 3) =====
def is_delivery_order(receipt_text):
    """영수증 헤더에 배달 키워드가 있으면 배달 주문으로 판단합니다."""
    header_lines = [line for line in receipt_text.split("\n") if line.strip()][:5]
    header = "\n".join(header_lines)
    return any(keyword in header for keyword in DELIVERY_KEYWORDS)


def is_store_copy(receipt_text):
    """'[매장용]' 영수증인지 판단합니다. ('[고객용]'이면 False)"""
    header = receipt_text[:200]
    if CUSTOMER_MARK in header:
        return False
    return STORE_MARK in header


def process_receipt(receipt_text):
    """수신된 영수증을 처리합니다.

    - 배달 주문 + [매장용] → 부자재 목록을 영수증 하단에 추가
    - 홀 주문 또는 [고객용] → 원본 그대로 유지
    (처리된 텍스트, 처리 설명) 튜플을 반환합니다.
    """
    delivery = is_delivery_order(receipt_text)
    store_copy = is_store_copy(receipt_text)

    if delivery and store_copy:
        result = process_store_data(receipt_text)
        menus = extract_menu_names(receipt_text)
        note = f"배달 주문([매장용]) — 부자재 목록 추가, 감지 메뉴: {menus}"
        return result, note

    if not delivery:
        note = "홀 주문 — 원본 그대로 출력"
    else:
        note = "배달 주문이지만 [고객용] — 원본 그대로 출력"
    return receipt_text, note


# ===== USB 프린터 연결 (요청사항 1) =====
def find_sam4s_printer(vid, pid):
    """config.json의 VID/PID로 SAM4S 프린터를 찾습니다."""
    if not USB_AVAILABLE:
        return None
    return usb.core.find(idVendor=vid, idPID=pid)


def open_printer_device(device):
    """프린터의 USB 인터페이스를 사용 가능한 상태로 준비합니다."""
    try:
        try:
            if device.is_kernel_driver_active(0):
                device.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass
        device.set_configuration()
        usb.util.claim_interface(device, 0)
        return device
    except usb.core.USBError:
        return None


def get_printer_endpoint(device):
    """프린터로 전송되는 데이터가 흐르는 BULK OUT 엔드포인트를 찾습니다."""
    cfg = device.get_active_configuration()
    for interface in cfg:
        endpoint = usb.util.find_descriptor(
            interface,
            custom_match=lambda ep: (
                usb.util.endpoint_direction(ep.bEndpointAddress)
                == usb.util.ENDPOINT_OUT
                and usb.util.endpoint_type(ep.bmAttributes)
                == usb.util.ENDPOINT_TYPE_BULK
            ),
        )
        if endpoint is not None:
            return endpoint
    return None


# ===== 실시간 수신 / 처리 루프 (요청사항 2) =====
def handle_receipt(raw_bytes, config):
    """영수증 1건을 처리합니다: 텍스트 변환 → 홀/배달 판단 → 저장 및 로그."""
    text = escpos_data_to_text(raw_bytes)
    if not text.strip():
        return

    log_message("수신된 데이터:\n" + text)
    print("[수신] 영수증 데이터 수신됨")

    result_text, note = process_receipt(text)
    log_message(f"처리 결과: {note}")

    output_path = save_receipt_output(result_text)
    log_message(f"결과 저장: {output_path}")
    print(f"[결과] {note} → {output_path}")

    debug_print(config, "=" * 40 + "\n" + result_text + "\n" + "=" * 40)


def save_receipt_output(result_text):
    """처리된 영수증을 output/ 폴더에 저장하고 경로를 반환합니다."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(OUTPUT_DIR, f"receipt_{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(result_text)
    return path


def receive_loop(device, config):
    """ESC/POS 데이터를 실시간으로 수신하고 영수증 단위로 처리합니다.

    - 용지 커터 명령(GS V)을 영수증 끝으로 판단합니다.
    - 커터 명령이 없어도 데이터 유입이 멈추면 일정 시간 후 확정 처리합니다.
    """
    endpoint = get_printer_endpoint(device)
    if endpoint is None:
        raise usb.core.USBError("데이터를 수신할 USB 엔드포인트를 찾을 수 없습니다.")

    buffer = bytearray()
    last_data_time = time.time()
    debug_print(config, f"수신 루프 시작 (endpoint: 0x{endpoint.bEndpointAddress:02x})")

    while True:
        try:
            chunk = endpoint.read(endpoint.wMaxPacketSize, timeout=USB_READ_TIMEOUT_MS)
        except usb.core.USBError as e:
            if "timed out" in str(e).lower():
                chunk = None  # 타임아웃 = 수신 데이터 없음 (정상)
            else:
                raise  # 연결 해제 등 실제 오류 → 상위에서 재연결 처리

        now = time.time()

        if chunk:
            buffer.extend(chunk)
            last_data_time = now
            debug_print(config, f"수신 {len(chunk)}바이트 (누적 {len(buffer)}바이트)")

            if len(buffer) > MAX_RECEIPT_BYTES:
                log_error(f"수신 버퍼가 {MAX_RECEIPT_BYTES}바이트를 초과하여 초기화합니다.")
                buffer.clear()
                continue

        # 용지 커터 명령(GS V) 발견 → 영수증 끝으로 판단하고 처리
        cut_index = buffer.find(_CUT_PREFIX)
        if cut_index >= 0:
            cut_end = cut_index + 3  # GS V + 파라미터 1바이트 포함
            receipt_bytes = bytes(buffer[:cut_end])
            del buffer[:cut_end]
            handle_receipt(receipt_bytes, config)
            continue

        # 데이터 유입이 멈춘 뒤 일정 시간이 지나면 남은 내용을 영수증으로 확정
        if buffer and (now - last_data_time) >= RECEIPT_IDLE_TIMEOUT_SEC:
            receipt_bytes = bytes(buffer)
            buffer.clear()
            handle_receipt(receipt_bytes, config)


def run_middleware(config):
    """메인 루프: 프린터 연결 대기 → 연결되면 실시간 수신 (연결 해제 시 재대기)."""
    vid = parse_hex(config["printer"]["vid"])
    pid = parse_hex(config["printer"]["pid"])

    print("=== POS Middleware 시작 ===")
    print(f"[설정] VID=0x{vid:04x} PID=0x{pid:04x} 디버그={'ON' if config['debug'] else 'OFF'}")
    log_message(
        f"미들웨어 시작 (VID=0x{vid:04x}, PID=0x{pid:04x}, "
        f"디버그={'ON' if config['debug'] else 'OFF'})"
    )

    if not USB_AVAILABLE:
        print("[경고] pyusb가 설치되어 있지 않습니다. USB 수신이 불가능하여 대기 상태로 유지됩니다.")
        print("       설치: pip install pyusb  (Windows는 libusb 드라이버도 필요)")
        log_error("pyusb 미설치 — USB 수신 불가")

    if vid == 0 and pid == 0:
        print("[경고] config.json의 VID/PID가 0x0000입니다. 실제 프린터 값으로 설정해주세요.")
        log_error("config.json의 VID/PID가 설정되지 않음(0x0000)")

    while True:
        try:
            device = find_sam4s_printer(vid, pid)
        except usb.core.USBError as e:
            print(f"[오류] USB 백엔드 오류: {e} — {USB_POLL_INTERVAL_SEC}초 후 재시도...")
            log_error(f"USB 백엔드 오류: {e}")
            time.sleep(USB_POLL_INTERVAL_SEC)
            continue

        if device is None:
            print(f"[오류] 프린터를 찾을 수 없습니다 (VID=0x{vid:04x}, PID=0x{pid:04x}). "
                  f"{USB_POLL_INTERVAL_SEC}초 후 재시도...")
            log_error(f"프린터 미연결 — 대기 중 (VID=0x{vid:04x}, PID=0x{pid:04x})")
            time.sleep(USB_POLL_INTERVAL_SEC)
            continue

        if open_printer_device(device) is None:
            print(f"[오류] 프린터 인터페이스를 열 수 없습니다. {USB_POLL_INTERVAL_SEC}초 후 재시도...")
            log_error("프린터 인터페이스 열기 실패 — 대기 중")
            time.sleep(USB_POLL_INTERVAL_SEC)
            continue

        print("[연결] SAM4S 프린터 연결됨. ESC/POS 데이터 수신 대기 중...")
        log_message("프린터 연결 성공")

        try:
            receive_loop(device, config)
        except usb.core.USBError as e:
            print(f"[오류] 프린터 연결이 끊어졌습니다 ({e}). 재연결 대기 중...")
            log_error(f"USB 오류로 연결 해제: {e}")
            time.sleep(USB_POLL_INTERVAL_SEC)
        except Exception as e:  # 예상치 못한 오류도 종료하지 않고 대기 상태 유지
            print(f"[오류] 처리 중 오류 발생: {e} — 대기 상태 유지")
            log_error(f"예상치 못한 오류: {e}\n{traceback.format_exc()}")
            time.sleep(USB_POLL_INTERVAL_SEC)
        # 연결이 끊겨도 종료하지 않고 while 루프에서 재연결을 시도합니다.


# ===== 기존 함수 (변경 없음) =====
def extract_menu_names(receipt_text):
    """영수증 텍스트에서 메뉴명을 추출합니다."""
    # 간단한 구현 예시
    lines = receipt_text.split('\n')
    menu_names = []
    for line in lines:
        for menu in MENU_MAPPING.keys():
            if menu in line:
                menu_names.append(menu)
    return menu_names


def get_sub_materials(menu_names):
    """메뉴명 리스트를 받아 부자재 목록을 생성합니다."""
    sub_materials = {}
    for menu in menu_names:
        # 세트 메뉴 처리 ('+' 구분)
        if '+' in menu:
            parts = menu.split('+')
            for part in parts:
                part = part.strip()
                if part in MENU_MAPPING:
                    for item, qty in MENU_MAPPING[part].items():
                        sub_materials[item] = sub_materials.get(item, 0) + qty
                elif part in OPTION_MAPPING:
                    for item, qty in OPTION_MAPPING[part].items():
                        sub_materials[item] = sub_materials.get(item, 0) + qty
        else:
            if menu in MENU_MAPPING:
                for item, qty in MENU_MAPPING[menu].items():
                    sub_materials[item] = sub_materials.get(item, 0) + qty
    return sub_materials


def format_receipt_with_materials(original_receipt, sub_materials):
    """원본 영수증에 부자재 목록을 추가합니다."""
    if not sub_materials:
        return original_receipt

    result = original_receipt.rstrip() + "\n\n--- 부자재 목록 ---\n"
    for item, qty in sub_materials.items():
        result += f"{item}({qty})\n"
    return result


def process_store_data(receipt_text):
    """[매장용] 영수증을 처리합니다."""
    print(f"[처리] 영수증 분석 중...")
    menus = extract_menu_names(receipt_text)
    print(f"[처리] 감지된 메뉴: {menus}")
    
    if not menus:
        return receipt_text
    
    sub_materials = get_sub_materials(menus)
    print(f"[처리] 필요한 부자재: {sub_materials}")
    
    return format_receipt_with_materials(receipt_text, sub_materials)


# ===== 테스트 코드 (--test 모드) =====
def run_tests():
    """내장 테스트. 기존 테스트를 유지하고 홀/배달 구분 테스트를 추가했습니다."""
    print("=== POS Middleware System (돈까스 매장) ===\n")

    # 테스트 영수증 (기존 테스트)
    test_receipt = """
[매장용] 알콜·한점배달 주문서
주문번호: 0017
더블 등심돈까스 1
마제소바 (미니공기밥포함) 1
"""

    print("[테스트 1] 원본 영수증:")
    print(test_receipt)

    result = process_store_data(test_receipt)

    print("\n[결과] 부자재 추가된 영수증:")
    print(result)

    # 홀/배달 구분 로직 테스트 (요청사항 3)
    print("\n" + "=" * 40)
    print("[테스트 2] 배달 + [매장용] → 부자재 목록 추가")
    result2, note2 = process_receipt(test_receipt)
    print(f"판단: {note2}")
    assert "부자재 목록" in result2, "배달 + [매장용]은 부자재가 추가되어야 합니다."
    print("통과")

    hall_receipt = """
[매장용] 홀 주문서
주문번호: 0018
더블 등심돈까스 1
"""
    print("\n[테스트 3] 홀 주문 + [매장용] → 원본 그대로")
    result3, note3 = process_receipt(hall_receipt)
    print(f"판단: {note3}")
    assert result3 == hall_receipt, "홀 주문은 원본이 유지되어야 합니다."
    print("통과")

    customer_receipt = """
[고객용] 배달 주문서
주문번호: 0019
더블 등심돈까스 1
"""
    print("\n[테스트 4] 배달 + [고객용] → 원본 그대로")
    result4, note4 = process_receipt(customer_receipt)
    print(f"판단: {note4}")
    assert result4 == customer_receipt, "[고객용]은 원본이 유지되어야 합니다."
    print("통과")

    # ESC/POS → 텍스트 변환 테스트 (요청사항 2)
    print("\n[테스트 5] ESC/POS 데이터 → 텍스트 변환")
    escpos_sample = (
        b"\x1b\x40"                          # ESC @ (초기화)
        + "[매장용] 배달 주문서\n".encode("cp949")
        + b"\x1b\x45\x01"                    # ESC E 1 (강조)
        + "더블 등심돈까스 1\n".encode("cp949")
        + b"\x1d\x56\x00"                    # GS V 0 (용지 커터)
    )
    converted = escpos_data_to_text(escpos_sample)
    print(converted)
    assert "더블 등심돈까스" in converted, "ESC/POS 변환이 올바르지 않습니다."
    assert "\x1b" not in converted and "\x1d" not in converted, "제어 코드가 남아 있습니다."
    print("통과")

    print("\n=== 모든 테스트 통과 ===")


def main():
    """진입점. --test 옵션으로 내장 테스트를 실행할 수 있습니다."""
    # 장시간 실행되는 데몬이므로 표준 출력을 라인 단위로 플러시합니다.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # 구버전 Python(3.6 이하)에서는 무시

    parser = argparse.ArgumentParser(description="POS Middleware - 부자재 자동 출력")
    parser.add_argument("--test", action="store_true", help="내장 테스트 실행")
    args = parser.parse_args()

    config = load_config()

    if args.test:
        run_tests()
        return

    try:
        run_middleware(config)
    except KeyboardInterrupt:
        print("\n[종료] 사용자에 의해 중단되었습니다.")
        log_message("사용자에 의해 종료됨")


if __name__ == "__main__":
    main()
