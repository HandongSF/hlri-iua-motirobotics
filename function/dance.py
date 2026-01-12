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
import pygame
import time
import os

base_dir = os.path.dirname(os.path.abspath(__file__))
MUSIC_FILE = os.path.join(base_dir, "SODA_POP.mp3")
START_SECONDS = 55  # 재생 시작 지점 (50초)
PLAY_DURATION = 40  # 재생할 시간 (50초)

pygame.init()
pygame.mixer.init()

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

def _music_stopper(duration_sec):
    """지정된 시간(초)만큼 기다린 후 음악을 정지시키는 함수"""
    print(f"⏰ 음악 타이머 시작: {duration_sec}초 후에 음악을 정지합니다.")
    time.sleep(duration_sec)
    pygame.mixer.music.stop()
    print("🛑 음악 타이머에 의해 재생이 종료되었습니다.")

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
    
def perform_head_nod(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, repetitions=2):
    """
    (신규) 고개를 부드럽게 끄덕이는 독립 실행 함수.
    가속도 프로파일을 사용하며, C.HEAD_NOD_HOME_POS(4000)을 준수합니다.
    """
    print(f"🤖 고개 끄덕이기 {repetitions}회 시작...")
    
    # 1. 동작 값 정의
    home_pos = C.HEAD_NOD_HOME_POS  # 4000 (들린 상태)
    down_pos = C.HEAD_NOD_DOWN_POS  # 3800 (숙인 상태)
    accel_value = 30                # 부드러운 가속 (0이 아닌 값)
    velocity_value = 300            # 끄덕이는 속도 (조절 가능)
    default_velocity = 100          # init.py의 기본 속도값
    nod_wait_time = 0.3             # 숙이고 머무는 시간
    nod_wait_time_up = 0.4          # 들고 머무는 시간
    
    # 2. 모터 설정 (가속도/속도) - try/finally로 안전하게
    try:
        with lock:
            print("  - 끄덕임용 가속도/속도 설정...")
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_VELOCITY, velocity_value)

        # 3. 끄덕임 동작 수행
        for i in range(repetitions):
            print(f"  - 끄덕 {i+1}회")
            with lock:
                # 고개 숙이기 (Down)
                io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_GOAL_POSITION, down_pos)
            time.sleep(nod_wait_time)
            
            with lock:
                # 고개 들기 (Home)
                io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_GOAL_POSITION, home_pos)
            time.sleep(nod_wait_time_up)
            
            # C.HEAD_NOD_MAX_POS (4030) 제약 조건은 
            # home_pos(4000)보다 크므로, 가속도 프로파일이 
            # 4000을 살짝 넘더라도 4030 안에서 안전합니다.

    finally:
        # 4. 모터 설정 초기화 (매우 중요!)
        with lock:
            print("  - 끄덕임 설정 초기화 (가속도 0, 속도 100)...")
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_VELOCITY, default_velocity) 
            
            # 마지막으로 홈 포지션으로 한 번 더 복귀 (안전을 위해)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_GOAL_POSITION, home_pos)
        
    print("✅ 고개 끄덕이기 완료!")
        
def _perform_shoulder_dance(pkt, port, lock, duration_sec, frequency_hz, title):
    """(수정) 사인파를 이용해 지정된 리듬으로 어깨를 흔드는 헬퍼 함수"""
    print(f"🎶 {title} 시작! ({duration_sec}초, {frequency_hz}Hz)")

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 리듬 조절 파라미터 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # dance_duration_sec = 4.0  <- 이제 외부 파라미터 사용
    # frequency_hz = 0.5        <- 이제 외부 파라미터 사용
    amplitude = C.SHOULDER_LEFT_POS - C.SHOULDER_CENTER_POS # 움직임의 폭
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    with lock:
        # 속도를 살짝 낮춰 더 부드럽게 만듭니다.
        io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 250)

    t0 = time.time()
    while True:
        t = time.time() - t0
        if t > duration_sec: # 파라미터로 받은 지속시간 사용
            break

        # 사인파 공식으로 현재 시간에 맞는 부드러운 위치 계산
        offset = amplitude * math.sin(2.0 * math.pi * frequency_hz * t) # 파라미터로 받은 빠르기 사용
        goal_pos = int(round(C.SHOULDER_CENTER_POS + offset))
        
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, goal_pos)
        
        time.sleep(0.02)

    # 어깨 춤이 끝나면 정확히 중앙으로 복귀
    with lock:
        io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
    time.sleep(0.5)
    print(f"✅ {title} 완료!")


def _new_dance_routine(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, shared_state: dict, home_pan: int, home_tilt: int, emotion_queue):
    try:
        # --- [준비] 춤 모드로 전환하고 고개를 정면으로! ---
        print("🤖 [춤 준비] 얼굴 추적 중지 및 고개 정렬")
        shared_state['mode'] = 'dancing'
        with lock:
            io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, home_pan)
            io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, home_tilt)
        time.sleep(0.5) # <<< 시간 1.0 -> 0.5
        
        print("🤖 팔 모터에 부드러운 가속도 설정...")
        with lock:
            # 0은 가속도 없음(기본값), 값이 클수록 느리게 가속됩니다. 20~50 사이 값으로 시작해보세요.
            accel_value = 30
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
        
        # 음악 준비
        pygame.mixer.music.load(MUSIC_FILE)

        print(f"{START_SECONDS}초부터 {PLAY_DURATION}초 동안 음악을 재생합니다.")
        pygame.mixer.music.play(start=START_SECONDS)

        stopper_thread = threading.Thread(target=_music_stopper, args=(PLAY_DURATION,), daemon=True)
        stopper_thread.start()

        _perform_shoulder_dance(pkt, port, lock, duration_sec=8.0, frequency_hz=0.5, title="오프닝 어깨 춤")
        _perform_shoulder_dance(pkt, port, lock, duration_sec=4.5, frequency_hz=1, title="고조되는 어깨 춤")
        time.sleep(0.25) # 다음 동작을 위해 잠시 대기

        # --- [안무 1단계] 몸 전체 왼쪽 회전 ---
        print("🤖 [안무 1단계] 몸 전체 왼쪽 회전 시작!")
        right_wheel_speed = -C.RIGHT_DIR * C.TURN_SPEED_UNITS * 2 # <<< 속도 2배 (기존 유지)
        left_wheel_speed = C.LEFT_DIR * C.TURN_SPEED_UNITS * 2    # <<< 속도 2배 (기존 유지)
        
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, right_wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, left_wheel_speed)
        time.sleep(0.3) # <<< 시간 0.6 -> 0.3
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        print("✅ [안무 1단계] 완료!")
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25

        # --- [안무 2단계] 왼팔 들기 ---
        print("🤖 [안무 2단계] 왼팔 들기 시작!")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 600) # <<< 속도 2배 (300 -> 600)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_UP_POS)
        time.sleep(0.35) # <<< 시간 0.7 -> 0.35
        print("✅ [안무 2단계] 완료!")
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25
        
        # --- [안무 3단계] 왼쪽 어깨 들었다 내리기 ---
        print("🤖 [안무 3단계] 왼쪽 어깨 들기 시작!")
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 500) # <<< 속도 2배 (250 -> 500)
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_LEFT_POS)
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25
        
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25
        print("✅ [안무 3단계] 완료!")
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25

        # --- [안무 4단계] 회전 후 팔 모으기 (동시 동작 방지) ---
        print("🤖 [안무 4단계] 회전 후 팔 모으기 시작!")

        # 1. 먼저 바퀴만 오른쪽으로 회전하여 원위치로 복귀합니다.
        right_wheel_speed = C.RIGHT_DIR * C.TURN_SPEED_UNITS * 2 # <<< 속도 2배 (기존 유지)
        left_wheel_speed = -C.LEFT_DIR * C.TURN_SPEED_UNITS * 2   # <<< 속도 2배 (기존 유지)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, right_wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, left_wheel_speed)

        # 2. 회전이 끝날 때까지 기다립니다.
        time.sleep(0.3) # <<< 시간 0.6 -> 0.3

        # 3. 팔을 움직이기 전에, 바퀴를 명시적으로 완전히 정지시킵니다.
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        time.sleep(0.15) # <<< 시간 0.3 -> 0.15

        # 4. 바퀴가 멈춘 후에 팔과 손 동작을 순차적으로 수행합니다.
        print(" - 팔 중간 위치로 들어올리기!")
        with lock:
            # 팔/손 속도 설정 추가
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 800) # <<< 속도 2배
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 800)  # <<< 속도 2배
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_MIDDLE_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_MIDDLE_POS)
        time.sleep(0.35) # <<< 시간 0.7 -> 0.35

        print(" - 팔/손 액션 위치로 이동!")
        with lock:
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, 600) # <<< 속도 2배
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, 600)  # <<< 속도 2배
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_ACTION_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_ACTION_POS)
        time.sleep(0.5) # <<< 시간 1.0 -> 0.5
        
        print("  - (부담 완화) 양손 잠시 휴식 (토크 OFF)")
        with lock:
            # 12번 모터의 토크를 꺼서 힘을 빼줍니다.
            io.write1(pkt, port, C.LEFT_HAND_ID, C.ADDR_TORQUE_ENABLE, 0)
            # 8번 오른손 모터의 토크도 함께 꺼줍니다.
            io.write1(pkt, port, C.RIGHT_HAND_ID, C.ADDR_TORQUE_ENABLE, 0)
        
        print("✅ [안무 4단계] 완료!")
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25

        # --- [안무 5단계] 스텝 & 팔 동작 ---
        print("🤖 [안무 5단계] 스텝 및 팔 동작 시작!")
        
        # 5-1. 몸 전체 스텝 (좌 -> 원위치 -> 우 -> 원위치 -> 좌)
        step_speed = C.TURN_SPEED_UNITS * 2 # <<< 속도 2배
        step_duration = 0.15 # <<< 시간 0.3 -> 0.15

        # 왼쪽으로 살짝
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -step_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -step_speed)
        time.sleep(step_duration)
        # 원위치
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, step_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, step_speed)
        time.sleep(step_duration)
        # 오른쪽으로 살짝
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, step_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, step_speed)
        time.sleep(step_duration)
        # 원위치
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -step_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -step_speed)
        time.sleep(step_duration)
        # 마지막 왼쪽으로 이동 (1단계와 동일한 회전)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -C.RIGHT_DIR * C.TURN_SPEED_UNITS * 2) # <<< 속도 2배
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, C.LEFT_DIR * C.TURN_SPEED_UNITS * 2)    # <<< 속도 2배
        time.sleep(0.6) # <<< 시간 1.2 -> 0.6
        # 스텝 종료 후 바퀴 정지
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25
        
        print(" - 고개 오른쪽으로!")
        with lock:
            io.write4(pkt, port, C.HEAD_PAN_ID, C.ADDR_PROFILE_VELOCITY, 400) # <<< 속도 2배 (임의 값, 조절 필요)
            goal_pos = home_pan - C.HEAD_PAN_OFFSET 
            io.write4(pkt, port, C.HEAD_PAN_ID, C.ADDR_GOAL_POSITION, goal_pos)
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25

        # 5-2. 팔 동작 (위 -> 중간 -> 아래)
        arm_speed = 800 # <<< 속도 2배 (400 -> 800)
        arm_wait_time = 0.3 # <<< 시간 0.6 -> 0.3

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            
            print(" - 팔 위로!")
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
        time.sleep(arm_wait_time)
        
        with lock:
            print(" - 팔 중간으로!")
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_MIDDLE_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_MIDDLE_POS)
        time.sleep(arm_wait_time)

        with lock:
            print(" - 팔 아래로!")
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(arm_wait_time)

        print("✅ [안무 5단계] 완료!")
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25

        # --- [안무 6단계] 만세 동작 ---
        print("🤖 [안무 6단계] 만세 동작 시작!")
        arm_speed = 1000 # <<< 속도 2배 (500 -> 1000)
        arm_wait_time = 0.3 # <<< 시간 0.6 -> 0.3

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            
            print(" - 만세!")
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
        time.sleep(arm_wait_time)
        
        with lock:
            print(" - 원위치!")
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(arm_wait_time)

        print("✅ [안무 6단계] 완료!")
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25

        # --- [안무 7단계] 어깨 춤 (속도 유지) ---
        print("🤖 [안무 7단계] 어깨 춤 시작! (원래 속도)")
        shoulder_speed = 400 # <<< 속도 유지
        shoulder_wait_time = 0.3 # <<< 시간 유지

        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, shoulder_speed)

        for i in range(3):
            print(f" - 어깨 춤: {i + 1}번째")
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_RIGHT_POS)
            time.sleep(shoulder_wait_time)
            
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_LEFT_POS)
            time.sleep(shoulder_wait_time)

        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25
        
        # 어깨 춤 이후 동작은 다시 2배속 적용
        print(" - 고개 정면으로 원위치!")
        with lock:
            io.write4(pkt, port, C.HEAD_PAN_ID, C.ADDR_PROFILE_VELOCITY, 400) # <<< 속도 2배
            io.write4(pkt, port, C.HEAD_PAN_ID, C.ADDR_GOAL_POSITION, home_pan)
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25
        
        # 오른쪽으로 이동
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, C.RIGHT_DIR * C.TURN_SPEED_UNITS * 2) # <<< 속도 2배
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * C.TURN_SPEED_UNITS * 2)   # <<< 속도 2배
        time.sleep(0.6) # <<< 시간 1.2 -> 0.6
        
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        
        print(" - 팝 포즈!")
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 1000) # <<< 속도 2배
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 1000)  # <<< 속도 2배
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(0.25) # <<< 시간 0.5 -> 0.25

        print("✅ [안무 7단계] 완료!")
        
        # --- [안무 8단계] 마무리 동작 ---
        print("🤖 [안무 8단계] 마무리 동작 시작!")

        # 1. 팔 교차 동작 (3회 반복)
        arm_speed = 600  # <<< 속도 2배 (500 -> 1000)
        arm_wait_time = 0.25 # <<< 시간 0.5 -> 0.25
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)

        print(" - 팔 교차 1/3")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
        time.sleep(arm_wait_time)

        print(" - 팔 교차 2/3")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
        time.sleep(arm_wait_time)

        print(" - 팔 교차 3/3")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
        time.sleep(arm_wait_time)

        # 2. 오른손 안쪽으로 모으기
        print(" - 오른손 모으기")
        with lock:
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, 600) # <<< 속도 2배
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_ACTION_POS)
        time.sleep(0.35) # <<< 시간 0.7 -> 0.35

        # 3. 몸통 오른쪽으로 살짝 돌렸다 원위치 (2회 반복)
        print(" - 몸통 트위스트")
        twist_duration = 0.15 # <<< 시간 0.3 -> 0.15
        twist_speed = C.TURN_SPEED_UNITS * 2 # <<< 속도 2배

        for i in range(2):
            print(f" - 트위스트 {i + 1}회")
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID,-C.RIGHT_DIR * twist_speed)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, C.LEFT_DIR * twist_speed)
            time.sleep(twist_duration)
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, C.RIGHT_DIR * twist_speed)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * twist_speed)
            time.sleep(twist_duration)
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)

        time.sleep(0.5) # <<< 시간 1.0 -> 0.5
        
        with lock:
            print(" - 원위치!")
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(arm_wait_time)

        print("✅ [안무 8단계] 완료!")
        print("☕ [숨 고르기] 다음 동작 전 1.5초 휴식...")
        time.sleep(1.5)
        # 피날레: 3초간 1.2Hz의 빠르고 역동적인 리듬으로 어깨 춤
        _perform_shoulder_dance(pkt, port, lock, duration_sec=11.0, frequency_hz=1, title="피날레 어깨 춤")
        time.sleep(0.25) # 다음 동작을 위해 잠시 대기
        
        print("🤖 [마무리 준비] 양손 토크 ON 및 자세 복귀")
        with lock:
            # 12번 왼손 모터의 토크를 다시 켜고,
            io.write1(pkt, port, C.LEFT_HAND_ID, C.ADDR_TORQUE_ENABLE, 1)
            # 8번 오른손 모터의 토크도 다시 켭니다.
            io.write1(pkt, port, C.RIGHT_HAND_ID, C.ADDR_TORQUE_ENABLE, 1)
            
            # 안전하게 준비 자세로 미리 이동시킵니다.
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
        time.sleep(0.5)

    finally:
        pygame.mixer.music.stop()
        print("🤖 팔 모터 가속도 설정 초기화...")
        with lock:
            # 가속도 설정을 0으로 되돌려 원래의 빠른 반응 속도로 복구합니다.
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
        shared_state['mode'] = 'tracking'
        if emotion_queue:
            emotion_queue.put("NEUTRAL")
        print("🎉🎉 춤 시퀀스 종료! 얼굴 추적 모드로 즉시 전환합니다.")

        try:
            print("🤖 [마무리] 모든 모터를 초기 자세로 되돌립니다.")

            # 1. with lock 블록은 io.write4 함수들에만 적용합니다.
            with lock:
                io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
                io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
                io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
                io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)

            # 2. wheel.set_wheel_speed 함수는 lock 블록 밖에서 호출합니다.
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)

            time.sleep(1.0)
            print("✅ 모든 모터 원위치 복귀 완료.")
        except Exception as e:
            print(f"  ⚠️ 춤 종료 후 모터 원위치 복귀 중 오류 발생: {e}")


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
        

def start_new_dance(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, shared_state: dict, home_pan: int, home_tilt: int, emotion_queue):
    threading.Thread(target=_new_dance_routine, args=(port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue), daemon=True).start()
    return PLAY_DURATION

def play_greeting_motion(port: PortHandler, pkt: PacketHandler, lock):
    """
    (수정) 부드러운 가속도를 적용하여 인사하는 동작을 수행합니다.
    """
    print("🤖 [행동] 부드러운 인사 동작 시작...")
    
    # 가속도 값 설정 (값이 작을수록 더 부드럽고 느리게 가속/감속합니다. 0은 즉시 가속)
    acceleration_value = 30 
    
    try:
        # --- 동작 시작 전: 가속도 설정 ---
        print(f"  - 부드러운 움직임을 위해 가속도 값을 {acceleration_value}(으)로 설정합니다.")
        with lock:
            # 팔과 손 모터에 가속도 값을 적용합니다.
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)

        # 동작에 사용할 속도를 미리 정의
        ARM_SPEED = 500
        HAND_SPEED = 600
        
        # --- 기존 동작 순서는 동일하게 유지 ---
        
        # 1. 왼손을 바깥쪽으로 이동시킵니다.
        print("  - 1. 왼손 바깥으로 이동")
        with lock:
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, HAND_SPEED)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_WAVE_OUT_POS) # 대기 시간 소폭 증가

        # 2. 왼팔을 위로 듭니다.
        print("  - 2. 왼팔 위로 들기")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, ARM_SPEED)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_UP_POS)
        time.sleep(1.2) # 대기 시간 소폭 증가

        # 3. 왼손을 원위치와 바깥쪽으로 2번 반복하여 흔듭니다.
        print("  - 3. 손 흔들기 (2회 반복)")
        for i in range(2):
            print(f"    - 손 흔들기 {i+1}회: 원위치로")
            with lock:
                io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
            time.sleep(0.5) # 대기 시간 소폭 증가
            
            print(f"    - 손 흔들기 {i+1}회: 바깥으로")
            with lock:
                io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_WAVE_OUT_POS)
            time.sleep(0.5) # 대기 시간 소폭 증가

        # 4. 왼팔을 원위치로 내립니다.
        print("  - 4. 왼팔 내리기")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
        # 5. 왼손을 원위치 시킵니다.
        print("  - 5. 왼손 원위치")
        with lock:
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
        time.sleep(0.7) # 대기 시간 소폭 증가

        print("✅ [행동] 인사 동작 완료.")

    except Exception as e:
        print(f"❌ 인사 동작 중 오류 발생: {e}")
    finally:
        # --- 동작 종료 후: 가속도 초기화 (매우 중요!) ---
        print("  - 가속도 설정을 기본값(0)으로 되돌립니다.")
        with lock:
            # 다른 동작에 영향을 주지 않도록 가속도 값을 0으로 반드시 되돌립니다.
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            
def play_both_arms_motion(port: PortHandler, pkt: PacketHandler, lock):
    """(수정) 양팔을 동시에 들었다 내리면서, 손을 펼쳤다 오므리는 동작"""
    print("🤖 [행동] 양팔 펼치며 들기 동작 시작...")
    
    acceleration_value = 40  # 부드러운 움직임을 위한 가속도
    arm_speed = 120          # 천천히 움직이는 팔 속도
    hand_speed = 400         # 손 움직임 속도
    
    try:
        # 1. 동작 시작 전, 팔과 손 모터 모두에 가속도와 속도 설정
        with lock:
            # 가속도 설정
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            
            # 속도 설정
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, hand_speed)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, hand_speed)

        # 2. 양팔을 위로 올리면서, 동시에 양손을 바깥으로 펼칩니다.
        print("  - 양팔 올리며 손 펼치기...")
        with lock:
            # 팔 올리기
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
            # 손 펼치기
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_WAVE_OUT_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_WAVE_OUT_POS)
        time.sleep(3.5) # 동작 완료 대기

        # 3. 양팔을 준비 위치로 내리면서, 동시에 양손을 원위치로 오므립니다.
        print("  - 양팔 내리며 손 오므리기...")
        with lock:
            # 팔 내리기
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
            # 손 오므리기
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
        time.sleep(2.5) # 동작 완료 대기
        
        print("✅ [행동] 양팔 펼치며 들기 동작 완료.")

    finally:
        # 4. 동작 종료 후, 사용된 모든 모터의 가속도 설정을 반드시 초기화합니다.
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)


def play_right_arm_motion(port: PortHandler, pkt: PacketHandler, lock):
    """오른팔만 부드럽게 들었다 내리는 동작"""
    print("🤖 [행동] 오른팔 단독 동작 시작...")
    
    acceleration_value = 40
    arm_speed = 120
    
    try:
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)

        print("  - 오른팔 올리기...")
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
        time.sleep(2.5)

        print("  - 오른팔 내리기...")
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
        time.sleep(2.5)
        
        print("✅ [행동] 오른팔 단독 동작 완료.")

    finally:
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)


def play_left_arm_motion(port: PortHandler, pkt: PacketHandler, lock):
    """왼팔만 부드럽게 들었다 내리는 동작"""
    print("🤖 [행동] 왼팔 단독 동작 시작...")
    
    acceleration_value = 40
    arm_speed = 120
    
    try:
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)

        print("  - 왼팔 올리기...")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
        time.sleep(2.5)

        print("  - 왼팔 내리기...")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
        time.sleep(2.5)

        print("✅ [행동] 왼팔 단독 동작 완료.")
        
    finally:
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            
# mk2/dance.py (기존 코드에 이어서 추가)

def play_shy_motion(port: PortHandler, pkt: PacketHandler, lock: threading.Lock):
    """
    (신규) 부끄부끄 동작: 팔을 아래로 모으고 몸을 배배 꼬는 동작
    """
    print("🤖 [행동] 부끄부끄(Shy) 동작 시작...")

    # 1. 설정값 정의
    accel_value = 40        # 부드러운 가속
    arm_speed = 150         # 천천히 내리기
    hand_speed = 150        # 천천히 모으기
    wheel_speed = C.TURN_SPEED_UNITS  # 천천히 이동
    
    # 어깨 춤 파라미터 (느리고 부드럽게)
    shoulder_wiggle_count = 2
    shoulder_wait = 0.8     # 한 쪽으로 갔다가 머무는 시간

    try:
        # --- [1단계] 움츠리기 (팔 내리기 + 손 모으기 + 바퀴 이동) ---
        print("  - 1. 팔 내리고 손 모으면서 오른쪽으로 살짝 이동...")
        
        with lock:
            # 가속도 및 속도 설정
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, hand_speed)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, hand_speed)

            # 목표 위치 전송 (동시 수행)
            # 팔: 아래로 (DOWN_POS가 이미 아래라면 READY_POS보다 더 안쪽이나 아래일 수 있음 확인 필요)
            # 여기서는 '모으는 느낌'을 위해 ACTION_POS(살짝 위)보다는 DOWN_POS나 READY_POS를 사용하되,
            # 손을 안쪽으로(ACTION_POS) 모으는 것이 핵심입니다.
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
            
            # 손: 안쪽으로
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_ACTION_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_ACTION_POS)
            
            # 고개: 살짝 숙이기 (옵션 - 더 부끄러워 보임)
            # io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, 3800) 

        # 바퀴: 오른쪽으로 살짝 이동 (몸 비틀기 시뮬레이션)
        # 0.5초 동안만 이동
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -C.RIGHT_DIR * wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * wheel_speed) # 오른쪽 이동은 (우: -, 좌: -) ? 확인 필요.
        # 기존 코드 참고: 오른쪽 이동 = R(-), L(-) 
        # (wheel.py의 compute_cmd 로직이나 dance.py의 안무 1단계 참고)
        # 안무 1단계(왼쪽 회전): R(-), L(+)
        # 안무 5단계(오른쪽 이동): R(+), L(-)  <-- 이걸로 추정됨.
        # 따라서 오른쪽 이동:
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, C.RIGHT_DIR * wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * wheel_speed)
        
        time.sleep(0.5) 
        
        # 바퀴 정지
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        
        # 팔 동작 완료 대기
        time.sleep(1.0) 

        # --- [2단계] 몸 배배 꼬기 (어깨 춤 느리게) ---
        print("  - 2. 부끄러워서 몸 배배 꼬기...")
        
        with lock:
            # 어깨 속도 아주 느리게
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 100) 

        for i in range(shoulder_wiggle_count):
            # 왼쪽으로 으쓱
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_LEFT_POS)
            time.sleep(shoulder_wait)
            
            # 오른쪽으로 으쓱
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_RIGHT_POS)
            time.sleep(shoulder_wait)

        # 어깨 중앙 복귀
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
        time.sleep(0.5)

        # --- [3단계] 원위치 복귀 ---
        print("  - 3. 원위치로 복귀 (바퀴는 왼쪽으로 이동하여 복구)")
        
        # 바퀴: 아까 오른쪽으로 갔으니 왼쪽으로 이동하여 복귀
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -C.RIGHT_DIR * wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, C.LEFT_DIR * wheel_speed)
        time.sleep(0.5)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)

        with lock:
            # 팔/손 원위치
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
            
            # 고개 들기 (숙였었다면)
            # io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, C.TILT_POS_MAX) # 혹은 초기값

        time.sleep(1.0)
        print("✅ [행동] 부끄부끄 동작 완료.")

    except Exception as e:
        print(f"❌ 부끄부끄 동작 중 오류: {e}")

    finally:
        # 가속도 초기화
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)

def play_hug_motion(port: PortHandler, pkt: PacketHandler, lock: threading.Lock):
    """
    (신규) 포옹 동작: 팔을 벌리고 위로 들어올려 사용자를 안아주는 자세를 취함.
    """
    print("🤖 [행동] 포옹(Hug) 동작 시작...")

    # 부드러운 포옹을 위한 설정
    accel_value = 30         # 부드러운 가속
    arm_speed = 100          # 천천히 벌리기 (감성적)
    wait_time = 10.0          # 포옹 유지 시간

    try:
        # 1. 가속도 및 속도 설정 (부드럽게)
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)

        # 2. 팔을 들고 손을 넓게 벌리기
        print("  - 이리 와요, 안아줄게요. (팔 벌림)")
        with lock:
            # 팔은 위로 (TOP_POS)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
            
            # 손은 바깥으로 넓게 (WAVE_OUT_POS 이용)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_WAVE_OUT_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_WAVE_OUT_POS)

        # 3. 포옹 자세 유지
        time.sleep(wait_time)

        # 4. 원위치 복귀
        print("  - 제자리로...")
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
        
        time.sleep(1.5) # 복귀 시간 대기

        print("✅ [행동] 포옹 동작 완료.")

    except Exception as e:
        print(f"❌ 포옹 동작 중 오류: {e}")

    finally:
        # 가속도 초기화 (필수)
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)