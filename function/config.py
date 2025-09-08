# function/config.py

import os
import platform
# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 1. 라이브러리 import 추가 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
try:
    import serial.tools.list_ports
except ImportError:
    print("⚠️ 'pyserial' 라이브러리가 필요합니다. 'pip install pyserial' 명령어로 설치해주세요.")
    serial = None
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

# ---- DXL Control Table ----
ADDR_OPERATING_MODE   = 11
ADDR_TORQUE_ENABLE    = 64
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION    = 116
ADDR_PRESENT_POSITION = 132
ADDR_GOAL_VELOCITY    = 104

# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 2. 자동 포트 검색 함수 추가 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
def find_dxl_port() -> str | None:
    """
    PC에 연결된 시리얼 포트 목록을 스캔하여 'U2D2', 'USB Serial', 'FTDI' 등
    다이나믹셀 제어기와 관련된 키워드를 포함한 포트를 찾아 반환합니다.
    """
    if serial is None:
        return None

    print("▶️  사용 가능한 시리얼 포트 검색 중...")
    ports = serial.tools.list_ports.comports()
    dxl_port = None
    
    for port in ports:
        print(f"  - 포트: {port.device}, 설명: {port.description}")
        # U2D2 또는 일반적인 USB-to-Serial 컨버터(FTDI 칩셋)를 식별하기 위한 키워드
        if 'U2D2' in port.description or \
           'USB Serial Port' in port.description or \
           'FTDI' in port.description:
            dxl_port = port.device
            print(f"✅ 다이나믹셀 포트를 찾았습니다: {dxl_port}")
            break # 첫 번째로 찾은 포트를 사용

    if dxl_port is None:
        print("⚠️  자동으로 다이나믹셀 포트를 찾지 못했습니다.")

    return dxl_port
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

# ---- 기본 HW ----
_IS_WINDOWS = (platform.system() == "Windows")
_DEFAULT_PORT = "COM3" if _IS_WINDOWS else "/dev/tty.usbmodemXXXX"

# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 3. 포트 설정 로직 수정 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# 1. 환경변수(.env.local)에 DXL_PORT가 지정되어 있으면 그 값을 최우선으로 사용합니다.
# 2. 환경변수가 없으면, find_dxl_port() 함수를 호출하여 포트를 자동으로 찾습니다.
# 3. 자동 찾기에도 실패하면, 기존의 OS별 기본값(_DEFAULT_PORT)을 사용합니다.
MANUAL_PORT = os.getenv("DXL_PORT")
if MANUAL_PORT:
    print(f"ℹ️  .env.local에 지정된 포트({MANUAL_PORT})를 사용합니다.")
    DEVICENAME = MANUAL_PORT
else:
    DEVICENAME = find_dxl_port() or _DEFAULT_PORT
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

BAUDRATE         = int(os.getenv("DXL_BAUD", "57600"))
PROTOCOL_VERSION = float(os.getenv("DXL_PROTO", "2.0"))

# ---- 팬/틸트(Position) ----
PAN_ID, TILT_ID = 2, 9
SERVO_MIN, SERVO_MAX = 0, 4095
KP_PAN, KP_TILT = 0.05, 0.05
DEAD_ZONE = 50
MAX_PIXEL_OFF = 200
PROFILE_VELOCITY = 100  # position mode에서 이동 속도 프로파일

# ---- 휠(Velocity) ----
LEFT_ID, RIGHT_ID = 4, 3
LEFT_DIR, RIGHT_DIR = -1, +1
RPM_PER_UNIT = 0.229
BASE_RPM = float(os.getenv("BASE_RPM", "25.0"))
TURN_RPM = float(os.getenv("TURN_RPM", "25.0"))
VEL_MIN, VEL_MAX = -1023, +1023

def rpm_to_unit(rpm: float) -> int:
    return int(round(rpm / RPM_PER_UNIT))

BASE_SPEED_UNITS = rpm_to_unit(BASE_RPM)
TURN_SPEED_UNITS = rpm_to_unit(TURN_RPM)

# ---- 댄스(2XL430) ----
DANCE_ID = 5
AUX_ID   = 6  # 현재 미사용(포지션/토크온만)
EXTRA_POS_IDS = (DANCE_ID, AUX_ID)

DANCE_AMP = int(os.getenv("DANCE_AMP", "140"))    # ticks (±)
DANCE_HZ  = float(os.getenv("DANCE_HZ",  "1.2"))  # Hz