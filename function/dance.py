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

# mk2/dance.py
import time, math, threading
from dynamixel_sdk import PortHandler, PacketHandler
from . import config as C, dxl_io as io
from . import wheel
import time

_dance_event = threading.Event()
_dance_thread = None
_dance_origin_pos = None

# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 1. 추가된 부분 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
def play_rps_motion(port: PortHandler, pkt: PacketHandler, lock):
    """가위바위보 게임 시 팔을 3번 위아래로 움직이는 함수"""
    print("🤖 가위바위보 팔 동작 시작...")
    
    # 동작을 수행하기 전에 팔 모터의 현재 위치를 읽어옵니다.
    # 이렇게 하면 동작이 끝난 후 원래 위치로 돌아갈 수 있습니다.
    initial_pos = io.read_present_position(pkt, port, lock, C.RPS_ARM_ID)

    with lock:
        # 3번 반복
        for _ in range(3):
            # 팔 올리기
            io.write4(pkt, port, C.RPS_ARM_ID, C.ADDR_GOAL_POSITION, C.RPS_ARM_UP_POS)
            time.sleep(0.5) # 잠시 대기
            # 팔 내리기 (시작 위치)
            io.write4(pkt, port, C.RPS_ARM_ID, C.ADDR_GOAL_POSITION, C.RPS_ARM_DOWN_POS)
            time.sleep(0.5) # 잠시 대기
    
    # 혹시 모르니 마지막에 한 번 더 시작 위치로 팔을 내립니다.
    with lock:
        io.write4(pkt, port, C.RPS_ARM_ID, C.ADDR_GOAL_POSITION, initial_pos)

    print("✅ 가위바위보 팔 동작 완료.")
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

def _worker(port: PortHandler, pkt: PacketHandler, lock, origin: int, amp: int, hz: float):
    t0 = time.perf_counter()
    print(f"💃 DANCE start @pos={origin}, amp=±{amp}, hz={hz}")
    try:
        while _dance_event.is_set():
            t = time.perf_counter() - t0
            offset = int(round(amp * math.sin(2.0 * math.pi * hz * t)))
            goal = int(io.clamp(origin + offset, C.SERVO_MIN, C.SERVO_MAX))
            with lock:
                io.write4(pkt, port, C.DANCE_ID, C.ADDR_GOAL_POSITION, goal)
            time.sleep(0.03)
    finally:
        print("🛑 DANCE worker exit")

def start_dance(port: PortHandler, pkt: PacketHandler, lock, amp: int | None = None, hz: float | None = None):
    global _dance_thread, _dance_origin_pos
    if _dance_event.is_set():
        return
    _dance_origin_pos = io.read_present_position(pkt, port, lock, C.DANCE_ID)
    _dance_event.set()
    _dance_thread = threading.Thread(
        target=_worker,
        args=(port, pkt, lock, _dance_origin_pos, int(amp or C.DANCE_AMP), float(hz or C.DANCE_HZ)),
        name="dancer", daemon=True
    )
    _dance_thread.start()
    
def _new_dance_routine(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, shared_state: dict, home_pan: int, home_tilt: int):
    try:
        # --- [준비] 춤 모드로 전환하고 고개를 정면으로! ---
        print("🤖 [춤 준비] 얼굴 추적 중지 및 고개 정렬")
        shared_state['mode'] = 'dancing'
        with lock:
            io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, home_pan)
            io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, home_tilt)
        time.sleep(1.0)

        # --- [안무 1단계] 몸 전체 왼쪽 회전 ---
        print("🤖 [안무 1단계] 몸 전체 왼쪽 회전 시작!")
        right_wheel_speed = -C.RIGHT_DIR * C.TURN_SPEED_UNITS
        left_wheel_speed = C.LEFT_DIR * C.TURN_SPEED_UNITS
        
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, right_wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, left_wheel_speed)
        time.sleep(1.0)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        print("✅ [안무 1단계] 완료!")
        time.sleep(0.5)

        # --- [안무 2단계] 왼팔 들기 ---
        print("🤖 [안무 2단계] 왼팔 들기 시작!")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 300)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_UP_POS)
        time.sleep(0.7)
        print("✅ [안무 2단계] 완료!")
        time.sleep(0.5)
        
        # --- [안무 3단계] 왼쪽 어깨 들었다 내리기 ---
        print("🤖 [안무 3단계] 왼쪽 어깨 들기 시작!")
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 250)
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_LEFT_POS)
        time.sleep(0.5)
        
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
        time.sleep(0.5)
        print("✅ [안무 3단계] 완료!")
        time.sleep(0.5)

        # --- [안무 4단계] 회전하며 팔 모으기 ---
        print("🤖 [안무 4단계] 회전하며 팔 모으기 시작!")
        
        # 1. 바퀴를 오른쪽으로 회전 시작 (원위치 복귀)
        right_wheel_speed = C.RIGHT_DIR * C.TURN_SPEED_UNITS
        left_wheel_speed = -C.LEFT_DIR * C.TURN_SPEED_UNITS
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, right_wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, left_wheel_speed)
        
        # 2. 동시에 팔과 손을 '액션' 위치로 이동
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_ACTION_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_ACTION_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_ACTION_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_ACTION_POS)

        # 3. 회전과 팔 동작이 완료될 때까지 1초 기다림
        time.sleep(1.0)
        
        # 4. 바퀴 정지
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        
        # 5. 팔과 손을 다시 '준비' 자세로 복귀
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
            
        time.sleep(0.7) # 팔이 원위치로 돌아올 시간을 기다립니다.
        
        print("✅ [안무 4단계] 완료!")
        time.sleep(0.5)

        # (여기에 다음 안무가 추가될 예정입니다)

    finally:
        print("🎉🎉 새로운 춤 동작 모두 완료! 얼굴 추적을 다시 시작합니다.")
        shared_state['mode'] = 'tracking'

def stop_dance(port: PortHandler, pkt: PacketHandler, lock, return_home: bool = True, timeout: float = 2.0):
    global _dance_thread, _dance_origin_pos
    if not _dance_event.is_set():
        return
    _dance_event.clear()
    th = _dance_thread
    if th:
        th.join(timeout=timeout)
    _dance_thread = None
    if return_home and _dance_origin_pos is not None:
        goal = int(io.clamp(_dance_origin_pos, C.SERVO_MIN, C.SERVO_MAX))
        with lock:
            io.write4(pkt, port, C.DANCE_ID, C.ADDR_GOAL_POSITION, goal)
        print(f"↩️  DANCE return to origin: {goal}")
        
        
# 새로운 춤 전체를 관리할 함수 
def start_new_dance(port: PortHandler, pkt: PacketHandler, lock: threading.Lock):
    threading.Thread(target=_new_dance_routine, args=(port, pkt, lock), daemon=True).start()