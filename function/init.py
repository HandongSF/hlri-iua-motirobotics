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

# mk2/init.py
# function/init.py (파일 전체를 이 내용으로 교체해주세요)

from dynamixel_sdk import PortHandler, PacketHandler
from . import config as C
from . import dxl_io as io
import time

# 1번부터 12번까지 모든 모터의 목표 시작 위치를 정의합니다.
# 방금 측정한 디버그 값을 여기에 모두 넣어줍니다.
MOTOR_HOME_POSITIONS = {
    1: 4082,
    2: 2125,
    #3: 3307,
    #4: 1875,
    5: 2073,
    6: 2034,
    7: 3644,
    8: 1978,
    9: 2079,
    10: 957,
    11: 1452,
    12: 2053,
}

def init_all_motors_to_home_position(port: PortHandler, pkt: PacketHandler, lock):
    """모든 모터를 지정된 HOME 위치로 이동시키는 통합 초기화 함수"""
    print("▶️  모든 모터 초기화 및 지정 위치로 이동 시작...")
    
    with lock:
        for motor_id, home_pos in MOTOR_HOME_POSITIONS.items():
            # 1. 먼저 모든 모터의 토크를 켭니다. (운영 모드는 위치 제어로 가정)
            io.write1(pkt, port, motor_id, C.ADDR_TORQUE_ENABLE, 0) # 토크 껐다가
            io.write1(pkt, port, motor_id, C.ADDR_OPERATING_MODE, 3) # 위치 제어 모드로 설정
            io.write4(pkt, port, motor_id, C.ADDR_PROFILE_VELOCITY, 100) # 기본 이동 속도 설정
            io.write1(pkt, port, motor_id, C.ADDR_TORQUE_ENABLE, 1) # 토크 켜기
            
            # 2. 지정된 HOME 위치로 이동 명령을 내립니다.
            io.write4(pkt, port, motor_id, C.ADDR_GOAL_POSITION, home_pos)
            print(f"  [INIT] 모터 ID #{motor_id:02d} -> 목표 위치 {home_pos}로 이동 명령")

    # --- 바퀴 모터만 속도 제어 모드로 변경 ---
    print("▶️  바퀴 모터를 속도 제어(Velocity) 모드로 변경합니다...")
    with lock:
        for dxl_id in (C.LEFT_ID, C.RIGHT_ID):
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 0)
            io.write1(pkt, port, dxl_id, C.ADDR_OPERATING_MODE, 1)  # Velocity 모드
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 1)
    
    # 모든 모터가 움직일 시간을 잠시 기다립니다.
    print("▶️  모터가 초기 위치로 이동 중... (3초 대기)")
    time.sleep(3)
    print("✅ 모든 모터 초기화 완료!")


# 기존 함수들은 이제 새로운 통합 함수를 호출하도록 간단하게 변경합니다.
def init_pan_tilt_and_extras(port: PortHandler, pkt: PacketHandler, lock):
    # 이 함수는 이제 통합 함수에 의해 처리되므로 비워두거나 호출을 통합 함수로 넘깁니다.
    pass

def init_wheels(port: PortHandler, pkt: PacketHandler, lock):
    # 이 함수도 통합 함수에 의해 처리됩니다.
    pass

# launcher.py에서 최종적으로 호출될 함수는 이것 하나입니다.
def initialize_robot(port: PortHandler, pkt: PacketHandler, lock):
    init_all_motors_to_home_position(port, pkt, lock)


def stop_all_wheels(pkt: PacketHandler, port: PortHandler, lock):
    from . import wheel
    wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID,  0)
    wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
