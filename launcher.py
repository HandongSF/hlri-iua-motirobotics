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

from dynamixel_sdk import PortHandler, PacketHandler

# function 패키지에서 모듈을 올바르게 가져오도록 수정
from function import config as C
from function import init as I
from function import face as F
from function import wheel as W
from function import dance as D
from function import dxl_io as IO

# PTT (Space=녹음, ESC=종료, "춤"/"그만" 콜백)
from gemini_api import PressToTalk

# 통합된 display 앱을 실행하기 위한 함수 import
from display.main import run_face_app


def _get_env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or not str(v).strip() else str(v).strip()


def _default_cam_index() -> int:
    # macOS는 내장 카메라가 0번일 가능성이 높음
    return 0 if platform.system() == "Darwin" else 1


def _open_port() -> tuple[PortHandler, PacketHandler]:
    port = PortHandler(C.DEVICENAME)
    pkt = PacketHandler(C.PROTOCOL_VERSION)

    if not port.openPort():
        print(f"❌ 포트를 열 수 없습니다: {C.DEVICENAME}")
        sys.exit(1)
    if not port.setBaudRate(C.BAUDRATE):
        print(f"❌ Baudrate 설정 실패: {C.BAUDRATE}")
        try:
            port.closePort()
        finally:
            sys.exit(1)
    print(f"▶ 포트 열림: {C.DEVICENAME}, Baud={C.BAUDRATE}, Proto={C.PROTOCOL_VERSION}")
    return port, pkt


def _graceful_shutdown(port: PortHandler, pkt: PacketHandler, dxl_lock: threading.Lock):
    """댄스 정지 → 휠 0/토크 OFF → 포트 닫기"""
    print("▶ 시스템 종료 절차 시작...")
    try:
        D.stop_dance(port, pkt, dxl_lock, return_home=True)
    except Exception as e:
        print(f"  - 댄스 정지 중 오류: {e}")
    try:
        I.stop_all_wheels(pkt, port, dxl_lock)
    except Exception as e:
        print(f"  - 휠 정지 중 오류: {e}")
    try:
        with dxl_lock:
            ids = (C.PAN_ID, C.TILT_ID, *C.EXTRA_POS_IDS)
            for i in ids:
                IO.write1(pkt, port, i, C.ADDR_TORQUE_ENABLE, 0)
        print("  - 모든 모터 토크 OFF 완료")
    except Exception as e:
        print(f"  - 모터 토크 해제 중 오류: {e}")
    finally:
        try:
            port.closePort()
            print("■ 종료: 포트 닫힘")
        except Exception as e:
            print(f"  - 포트 닫기 중 오류: {e}")


def run_ptt(start_dance_cb, stop_dance_cb, emotion_queue, hotword_queue, stop_event):
    """PTT 스레드를 실행하는 타겟 함수"""
    try:
        app = PressToTalk(
            start_dance_cb=start_dance_cb,
            stop_dance_cb=stop_dance_cb,
            emotion_queue=emotion_queue,
            hotword_queue=hotword_queue,
            stop_event=stop_event  # stop_event 전달
        )
        app.run()
    except Exception as e:
        print(f"❌ PTT 스레드에서 치명적 오류 발생: {e}")
    finally:
        print("■ PTT 스레드 종료")


def main():
    print("▶ launcher: (통합 버전) FaceTrack + Wheels + PTT + Dance + Visual Face")
    print(f" - Port={C.DEVICENAME}, Baud={C.BAUDRATE}, Proto={C.PROTOCOL_VERSION}")

    port, pkt = _open_port()
    dxl_lock = threading.Lock()
    stop_event = threading.Event() # <<< 모든 스레드가 공유할 종료 신호

    emotion_queue = queue.Queue()
    hotword_queue = queue.Queue()

    def _handle_sigint(sig, frame):
        print("\n🛑 SIGINT(Ctrl+C) 감지 → 종료 신호 보냄")
        stop_event.set()
    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        I.init_pan_tilt_and_extras(port, pkt, dxl_lock)
        I.init_wheels(port, pkt, dxl_lock)
        print("▶ 초기화 완료: 팬/틸트 + 보조(Position), 휠(Velocity)")
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        _graceful_shutdown(port, pkt, dxl_lock)
        sys.exit(1)

    cam_default = str(_default_cam_index())
    cam_index = int(_get_env("CAM_INDEX", cam_default))
    t_face = threading.Thread(
        target=F.face_tracker_worker,
        args=(port, pkt, dxl_lock, stop_event),
        kwargs=dict(camera_index=cam_index, draw_mesh=False, print_debug=True),
        name="face",
        daemon=True,
    )

    t_visual_face = threading.Thread(
        target=run_face_app,
        args=(emotion_queue, hotword_queue, stop_event), # stop_event 전달
        name="visual_face",
        daemon=True,
    )

    start_dance = lambda: D.start_dance(port, pkt, dxl_lock)
    stop_dance  = lambda: D.stop_dance(port, pkt, dxl_lock, return_home=True)

    t_ptt = threading.Thread(
        target=run_ptt,
        args=(start_dance, stop_dance, emotion_queue, hotword_queue, stop_event), # stop_event 전달
        name="ptt",
        daemon=True,
    )

    # 모든 스레드 시작
    t_face.start()
    print(f"▶ FaceTracker 시작 (camera_index={cam_index})")
    t_visual_face.start()
    print("▶ Visual Face App 스레드 시작")
    t_ptt.start()
    print("▶ PTT App 스레드 시작")

    try:
        # 휠 제어는 메인 스레드에서 처리 (macOS는 별도 루프 필요 없음)
        if platform.system() == "Darwin":
            # macOS에서는 메인 스레드가 GUI 루프를 돌려야 함
            F.display_loop_main_thread(stop_event)
        else:
            W.wheel_loop(port, pkt, dxl_lock, stop_event)

    except KeyboardInterrupt:
        print("\n🛑 KeyboardInterrupt 감지 → 종료 신호 보냄")
        stop_event.set()
    finally:
        if not stop_event.is_set():
            stop_event.set()

        print("▶ 모든 스레드 종료 대기 중...")
        # 모든 스레드가 stop_event를 확인하고 종료할 시간을 줍니다.
        t_ptt.join(timeout=5.0)
        t_visual_face.join(timeout=2.0)
        t_face.join(timeout=2.0)
        
        _graceful_shutdown(port, pkt, dxl_lock)
        print("■ launcher 정상 종료")


if __name__ == "__main__":
    main()