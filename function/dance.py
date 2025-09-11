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