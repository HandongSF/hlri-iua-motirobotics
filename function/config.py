# ============================================================
#Licensed to the Apache Software Foundation (ASF) under one
#or more contributor license agreements.  See the NOTICE file
#distributed with this work for additional information
#regarding copyright ownership.  The ASF licenses this file
#to you under the Apache License, Version 2.0 (the
#"License"); you may not use this file except in compliance
#with the License.  You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
# ============================================================

# function/config.py

import os
import platform
try:
    import serial.tools.list_ports
except ImportError:
    print("⚠️ 'pyserial' 라이브러리가 필요합니다. 'pip install pyserial' 명령어로 설치해주세요.")
    serial = None

# ---- DXL Control Table ----
ADDR_OPERATING_MODE   = 11
ADDR_TORQUE_ENABLE    = 64
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION    = 116
ADDR_PRESENT_POSITION = 132
ADDR_GOAL_VELOCITY    = 104

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
        if 'U2D2' in port.description or \
           'USB Serial Port' in port.description or \
           'FTDI' in port.description:
            dxl_port = port.device
            print(f"✅ 다이나믹셀 포트를 찾았습니다: {dxl_port}")
            break

    if dxl_port is None:
        print("⚠️  자동으로 다이나믹셀 포트를 찾지 못했습니다.")

    return dxl_port

# ---- 기본 HW ----
_IS_WINDOWS = (platform.system() == "Windows")
_DEFAULT_PORT = "COM3" if _IS_WINDOWS else "/dev/tty.usbmodemXXXX"

MANUAL_PORT = os.getenv("DXL_PORT")
if MANUAL_PORT:
    print(f"ℹ️  .env.local에 지정된 포트({MANUAL_PORT})를 사용합니다.")
    DEVICENAME = MANUAL_PORT
else:
    DEVICENAME = find_dxl_port() or _DEFAULT_PORT

BAUDRATE         = int(os.getenv("DXL_BAUD", "57600"))
PROTOCOL_VERSION = float(os.getenv("DXL_PROTO", "2.0"))

# ---- 팬/틸트(Position) ----
PAN_ID, TILT_ID = 2, 9
SERVO_MIN, SERVO_MAX = 0, 4095
TILT_POS_MAX = 2030 # 👈 9번 모터의 최대 위치 제한값을 여기에 추가합니다.
PAN_SIGN = 1      # 👈 이 줄을 추가해주세요. (팬 모터 방향)
TILT_SIGN = -1    # 👈 이 줄을 추가해주세요. (틸트 모터 방향)
KP_PAN, KP_TILT = 0.08, 0.08       # ✅ P 게인 (엔진)
KI_PAN, KI_TILT = 0.005, 0.005     # ✅ I 게인 (미세조정) - 이 부분이 누락되었을 수 있습니다.
KD_PAN, KD_TILT = 0.06, 0.06       # ✅ D 게인 (브레이크)
DEAD_ZONE = 10
MAX_PIXEL_OFF = 200
PROFILE_VELOCITY = 150
MIN_MOVE_DELTA = 5

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
AUX_ID   = 6

# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 4. 수정된 부분 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# ---- 가위바위보 팔(Position) ----
RPS_ARM_ID = 11  # 팔 모터의 ID를 11번으로 수정
RPS_ARM_UP_POS = 1052 # 팔을 위로 올렸을 때의 위치 값 (예시)
RPS_ARM_DOWN_POS = 1352 # 시작 위치(118.83도)를 변환한 값

# ---- 초기화할 포지션 모드 모터 목록 ----
EXTRA_POS_IDS = (DANCE_ID, AUX_ID, RPS_ARM_ID)
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

DANCE_AMP = int(os.getenv("DANCE_AMP", "140"))
DANCE_HZ  = float(os.getenv("DANCE_HZ",  "1.2"))

# ---- 새로운 안무용 모터 ID ----
RIGHT_ARM_ID = 7
RIGHT_HAND_ID = 8
LEFT_HAND_ID = 12
LEFT_ARM_ID = 11
SHOULDER_ID = 5
HEAD_PAN_ID = 2

# ---- 새로운 안무용 모터 위치 값 (방향 수정 완료) ----

# [준비 자세 - HOME 위치]
RIGHT_ARM_READY_POS = 3644
LEFT_ARM_READY_POS = 1452
RIGHT_HAND_READY_POS = 1978
LEFT_HAND_READY_POS = 2053
SHOULDER_CENTER_POS = 2073

# [안무 2단계] 왼팔 번쩍 들기
LEFT_ARM_UP_POS = 800      # (값이 작아져야 올라감)

# [안무 3단계 & 7단계] 어깨 움직이기
SHOULDER_LEFT_POS = 2200   # (값이 커져야 왼쪽)
SHOULDER_RIGHT_POS = 1846  # (값이 작아져야 오른쪽)

# [안무 4단계] 팔 모으기
RIGHT_ARM_ACTION_POS = 3400  # 살짝 위로
LEFT_ARM_ACTION_POS = 1700   # 살짝 위로
RIGHT_HAND_ACTION_POS = 1500 # 안쪽으로
LEFT_HAND_ACTION_POS = 1500  # 안쪽으로

# [안무 5, 6, 8단계] 팔 위/중간/아래 동작
RIGHT_ARM_TOP_POS = 4050     # 👈 수정: 오른팔 위로 (값이 커져야 함)
LEFT_ARM_TOP_POS = 800       # 👈 수정: 왼팔 위로 (값이 작아져야 함)
RIGHT_ARM_MIDDLE_POS = 3850  # 👈 수정: 오른팔 중간
LEFT_ARM_MIDDLE_POS = 1100   # 👈 수정: 왼팔 중간
RIGHT_ARM_DOWN_POS = 3644    # 아래 (준비 자세와 동일)
LEFT_ARM_DOWN_POS = 1452     # 아래 (준비 자세와 동일)
HEAD_PAN_OFFSET = 400 # 👈 고개를 좌우로 움직일 각도 (값이 클수록 많이 움직입니다)

# ---- 댄스 전용 속도 프로파일 ----
# 1. 손/팔 등 관절 모터 속도 (Profile Velocity)
SPEED_NORMAL = 150  # 평상시 부드러운 속도
SPEED_DANCE = 300   # 춤 출때 2배 빠른 속도

# 2. 바퀴 모터 회전 속도 (Velocity)
# 기존 TURN_RPM(25.0)의 2배인 50.0으로 설정
DANCE_TURN_RPM = 50.0
DANCE_TURN_SPEED_UNITS = rpm_to_unit(DANCE_TURN_RPM)