# 임시 검증 스크립트 (검증 후 삭제됨)
# 프린터가 없는 상태에서 main.py가 종료되지 않고 대기 상태를 유지하는지 확인
import subprocess
import sys

proc = subprocess.Popen(
    [sys.executable, "main.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
try:
    out, _ = proc.communicate(timeout=8)
    print("프로세스가 스스로 종료됨 (비정상):")
    print(out)
except subprocess.TimeoutExpired:
    proc.terminate()
    out, _ = proc.communicate()
    print("8초 후에도 실행 중 → 대기 상태 유지 확인 (정상). 아래는 그동안의 출력:")
    print(out)
