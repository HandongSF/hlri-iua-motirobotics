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

# launcher.py
# ONE-PORT orchestrator: FaceTrack + Wheels + Gemini PTT + Dance + Visual Face
# - moti-face 앱을 별도 스레드로 실행하고, Queue를 통해 통신합니다.

from __future__ import annotations

import os
import sys
import signal
import threading
import platform
import queue
import multiprocessing

from dynamixel_sdk import PortHandler, PacketHandler

from function import config as C
from function import init as I
from function import face as F
from function import wheel as W
from function import dance as D
from function import dxl_io as IO

from gemini_api import PressToTalk
from display.main import run_face_app
from function.rock_paper import rock_paper_game_worker
from function.ox_game import ox_quiz_game_worker

def _get_env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or not str(v).strip() else str(v).strip()

def _default_cam_index() -> int:
    return 0 if platform.system() == "Darwin" else 1

def _open_port() -> tuple[PortHandler, PacketHandler]:
    port = PortHandler(C.DEVICENAME)
    pkt = PacketHandler(C.PROTOCOL_VERSION)

    if not port.openPort():
        print(f"❌ 포트를 열 수 없습니다: {C.DEVICENAME}")
        sys.exit(1)
    if not port.setBaudRate(C.BAUDRATE):
        print(f"❌ Baudrate 설정 실패: {C.BAUDRATE}")
        try: port.closePort()
        finally: sys.exit(1)
    print(f"▶ 포트 열림: {C.DEVICENAME}, Baud={C.BAUDRATE}, Proto={C.PROTOCOL_VERSION}")
    return port, pkt

def _graceful_shutdown(port: PortHandler, pkt: PacketHandler, dxl_lock: threading.Lock):
    print("▶ 시스템 종료 절차 시작...")
    try: D.stop_dance(port, pkt, dxl_lock, return_home=True)
    except Exception as e: print(f"  - 댄스 정지 중 오류: {e}")
    try: I.stop_all_wheels(pkt, port, dxl_lock)
    except Exception as e: print(f"  - 휠 정지 중 오류: {e}")
    try:
        with dxl_lock:
            # RPS_ARM_ID를 포함한 모든 모터 토크 OFF
            ids = (C.PAN_ID, C.TILT_ID, *C.EXTRA_POS_IDS, C.RPS_ARM_ID)
            for i in ids: IO.write1(pkt, port, i, C.ADDR_TORQUE_ENABLE, 0)
        print("  - 모든 모터 토크 OFF 완료")
    except Exception as e: print(f"  - 모터 토크 해제 중 오류: {e}")
    finally:
        try:
            port.closePort()
            print("■ 종료: 포트 닫힘")
        except Exception as e: print(f"  - 포트 닫기 중 오류: {e}")

def run_ptt(start_dance_cb, stop_dance_cb, play_rps_motion_cb, emotion_queue, hotword_queue, stop_event, rps_command_q, rps_result_q, sleepy_event, shared_state, ox_command_q):
    """PTT 스레드를 실행하는 타겟 함수"""
    try:
        app = PressToTalk(
            start_dance_cb=start_dance_cb,
            stop_dance_cb=stop_dance_cb,
            play_rps_motion_cb=play_rps_motion_cb,
            emotion_queue=emotion_queue,
            hotword_queue=hotword_queue,
            stop_event=stop_event,
            rps_command_q=rps_command_q,
            rps_result_q=rps_result_q,
            sleepy_event=sleepy_event,
            shared_state=shared_state,
            ox_command_q=ox_command_q 
        )
        app.run()
    except Exception as e: print(f"❌ PTT 스레드에서 치명적 오류 발생: {e}")
    finally: print("■ PTT 스레드 종료")

def main():
    print("▶ launcher: (통합 버전) FaceTrack + Wheels + PTT + Dance + Visual Face")
    print(f" - Port={C.DEVICENAME}, Baud={C.BAUDRATE}, Proto={C.PROTOCOL_VERSION}")

    port, pkt = _open_port()
    dxl_lock = threading.Lock()
    stop_event = threading.Event()
    emotion_queue = queue.Queue()
    hotword_queue = queue.Queue()
    rps_command_q = multiprocessing.Queue()
    rps_result_q = multiprocessing.Queue()
    ox_command_q = multiprocessing.Queue()
    video_frame_q = queue.Queue(maxsize=1)
    sleepy_event = threading.Event()
    shared_state = {'mode': 'tracking'}
    
    def _handle_sigint(sig, frame):
        print("\n🛑 SIGINT(Ctrl+C) 감지 → 종료 신호 보냄")
        stop_event.set()
    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        # 1. 통합 초기화 함수를 호출합니다. (이 함수는 init.py에 있어야 합니다)
        I.initialize_robot(port, pkt, dxl_lock)
        
        # 2. 춤이 끝난 후 돌아올 고개의 '가운데' 위치를 config.py에서 직접 가져옵니다.
        home_pan = I.MOTOR_HOME_POSITIONS.get(C.PAN_ID, 2048) # ID 10번 모터의 홈 위치
        home_tilt = I.MOTOR_HOME_POSITIONS.get(C.TILT_ID, 2048) # ID 9번 모터의 홈 위치

        print("▶ 초기화 완료: 모든 모터가 지정된 위치로 이동했습니다.")
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        _graceful_shutdown(port, pkt, dxl_lock)
        sys.exit(1)

    cam_default = str(_default_cam_index())
    cam_index = int(_get_env("CAM_INDEX", cam_default))

    t_face = threading.Thread(
        target=F.face_tracker_worker,
        args=(port, pkt, dxl_lock, stop_event, video_frame_q, sleepy_event, shared_state),
        kwargs=dict(camera_index=cam_index, draw_mesh=True, print_debug=True),
        name="face", daemon=True)

    # 3. 춤 시작 함수 호출 시 필요한 모든 정보(shared_state, home_pan, home_tilt)를 전달합니다.
    start_dance = lambda: D.start_new_dance(port, pkt, dxl_lock, shared_state, home_pan, home_tilt)
    stop_dance  = lambda: D.stop_dance(port, pkt, dxl_lock, return_home=True)
    play_rps_motion = lambda: D.play_rps_motion(port, pkt, dxl_lock)
    
    t_ptt = threading.Thread(
        target=run_ptt,
        args=(start_dance, stop_dance, play_rps_motion, emotion_queue, hotword_queue, stop_event, rps_command_q, rps_result_q, sleepy_event, shared_state, ox_command_q),
        name="ptt", daemon=True)

    t_visual_face = threading.Thread(
        target=run_face_app,
        args=(emotion_queue, hotword_queue, stop_event, sleepy_event, t_ptt),
        name="visual_face", daemon=True)
    
    t_rps_worker = threading.Thread(
        target=rock_paper_game_worker,
        args=(rps_command_q, rps_result_q, video_frame_q),
        name="rps_worker", daemon=True)
    
    t_ox_worker = threading.Thread(
        target=ox_quiz_game_worker,
        args=(ox_command_q, rps_result_q, video_frame_q), 
        name="ox_worker", daemon=True)
    
    t_wheels = threading.Thread(
        target=W.wheel_loop,
        args=(port, pkt, dxl_lock, stop_event),
        name="wheels", daemon=True)

    # ... (이하 스레드 시작 및 종료 코드는 동일합니다) ...
    t_face.start()
    print(f"▶ FaceTracker 시작 (camera_index={cam_index})")
    t_visual_face.start()
    print("▶ Visual Face App 스레드 시작")
    t_ptt.start()
    print("▶ PTT App 스레드 시작")
    t_rps_worker.start() 
    print("▶ 가위바위보 게임 스레드 시작")
    t_ox_worker.start()
    print("▶ OX 퀴즈 게임 스레드 시작")
    t_wheels.start()
    print("▶ Wheel 제어 스레드 시작")

    try:
        F.display_loop_main_thread(stop_event, window_name="Camera Feed (on Laptop)")
    except KeyboardInterrupt:
        print("\n🛑 KeyboardInterrupt 감지 → 종료 신호 보냄")
        stop_event.set()
    finally:
        if not stop_event.is_set(): stop_event.set()
        print("▶ 모든 스레드 종료 대기 중...")
        t_ptt.join(timeout=10.0)
        t_visual_face.join(timeout=15.0)
        t_face.join(timeout=3.0)
        t_rps_worker.join(timeout=5.0)
        t_ox_worker.join(timeout=5.0)
        t_wheels.join(timeout=3.0)
        _graceful_shutdown(port, pkt, dxl_lock)
        print("■ launcher 정상 종료")
        
if __name__ == "__main__":                                     
    main()