# 임시 검증 스크립트 (검증 후 삭제됨)
import main

cfg = main.load_config()
print("config 로드:", cfg)

# 실제 ESC/POS 수신 시뮬레이션: 배달+[매장용] 영수증 바이트
raw = (
    b"\x1b\x40"                                  # ESC @
    + "[매장용] 알콜·한점배달 주문서\n".encode("cp949")
    + b"\x1b\x61\x01"                            # ESC a 1 (가운데 정렬)
    + "더블 등심돈까스 1\n".encode("cp949")
    + "마제소바 (미니공기밥포함) 1\n".encode("cp949")
    + b"\x1d\x56\x00"                            # GS V 0 (커터)
)
main.handle_receipt(raw, cfg)

# 홀 주문 시뮬레이션: 원본 유지 확인
raw_hall = "[매장용] 홀 주문서\n".encode("cp949") + "더블 등심돈까스 1\n".encode("cp949")
main.handle_receipt(raw_hall, cfg)
