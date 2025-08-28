# mk2/config.py
# ------------------------------------------------------------
# 💡 .env.local 설정 가이드 (OS별)
#
# [Windows 예시]
#   DXL_PORT=COM3
#   DXL_BAUD=57600
#   DXL_PROTO=2.0
#
# [macOS 예시]
#   # 실제 연결된 U2D2/USB-시리얼 포트명으로 교체하세요.
#   # 터미널에서:  ls /dev/tty.*
#   DXL_PORT=/dev/tty.usbmodem1103
#   DXL_BAUD=57600
#   DXL_PROTO=2.0
#
# ※ .env.local의 값이 있으면 항상 그것이 우선 적용됩니다.
#    값이 없을 때만 OS별 기본값(Windows=COM3, macOS=/dev/tty.usbmodemXXXX)을 씁니다.
# ------------------------------------------------------------

import os
import platform

# ---- DXL Control Table ----
ADDR_OPERATING_MODE   = 11
ADDR_TORQUE_ENABLE    = 64
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION    = 116
ADDR_PRESENT_POSITION = 132
ADDR_GOAL_VELOCITY    = 104

# ---- 기본 HW ----
_IS_WINDOWS = (platform.system() == "Windows")
_DEFAULT_PORT = "COM3" if _IS_WINDOWS else "/dev/tty.usbmodemXXXX"  # macOS 기본 템플릿(실제 포트명으로 .env에서 지정 권장)

DEVICENAME       = os.getenv("DXL_PORT", _DEFAULT_PORT)
BAUDRATE         = int(os.getenv("DXL_BAUD", "57600"))
PROTOCOL_VERSION = float(os.getenv("DXL_PROTO", "2.0"))

# ---- 팬/틸트(Position) ----
PAN_ID, TILT_ID = 2, 9
SERVO_MIN, SERVO_MAX = 0, 4095
KP_PAN, KP_TILT = 0.3, 0.3
DEAD_ZONE = 5
MAX_PIXEL_OFF = 200
PROFILE_VELOCITY = 100  # position mode에서 이동 속도 프로파일

# ---- 휠(Velocity) ----
LEFT_ID, RIGHT_ID = 4, 3
LEFT_DIR, RIGHT_DIR = +1, -1
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
