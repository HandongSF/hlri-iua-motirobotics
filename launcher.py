# launcher.py
# ONE-PORT orchestrator: FaceTrack + Wheels + Gemini PTT + Dance
# - macOS 기본 CAM_INDEX=0, Windows 기본 CAM_INDEX=1 (env로 덮어쓰기 가능)
# - 포트 한 번만 열고 모든 모듈에서 공유
# - 안전 종료: ESC / Ctrl+C / '그만' → 댄스 정지·원위치, 휠 0, 토크 OFF, 포트 닫기

from __future__ import annotations

import os
import sys
import time
import signal
import threading
import platform

from dynamixel_sdk import PortHandler, PacketHandler

# function 패키지
from function import config as C
from function import init as I
from function import face as F
from function import wheel as W
from function import dance as D
from function import dxl_io as IO

# PTT (Space=녹음, ESC=종료, "춤"/"그만" 콜백)
from gemini_api import PressToTalk


def _get_env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or not str(v).strip() else str(v).strip()


def _default_cam_index() -> int:
    # macOS 기본 내장 카메라: 0 / Windows는 외장 카메라가 1인 경우가 많아 1 유지
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
    """댄스 정지 → 휠 0/토크 OFF → 팬/틸트 및 보조 포지션 토크 OFF → 포트 닫기"""
    try:
        # 댄스 중이면 정지·원위치
        try:
            D.stop_dance(port, pkt, dxl_lock, return_home=True)
        except Exception:
            pass

        # 휠 정지
        try:
            I.stop_all_wheels(pkt, port, dxl_lock)
        except Exception:
            pass

        # 포지션 모터 토크 OFF
        try:
            with dxl_lock:
                ids = (C.PAN_ID, C.TILT_ID, *C.EXTRA_POS_IDS)
                for i in ids:
                    IO.write1(pkt, port, i, C.ADDR_TORQUE_ENABLE, 0)
        except Exception:
            pass
    finally:
        try:
            port.closePort()
        except Exception:
            pass
        print("■ 종료: 포트 닫힘")


def run_ptt(start_dance_cb, stop_dance_cb):
    app = PressToTalk(start_dance_cb=start_dance_cb, stop_dance_cb=stop_dance_cb)
    app.run()
    print("■ PTT thread 종료")


def main():
    print("▶ launcher: ONE-PORT launcher (FaceTrack + Wheels + Gemini PTT + Dance)")
    print(f" - Port={C.DEVICENAME}, Baud={C.BAUDRATE}, Proto={C.PROTOCOL_VERSION}")

    # ---- DXL 포트 오픈 ----
    port, pkt = _open_port()

    # ---- 공용 락 & 종료 이벤트 ----
    dxl_lock = threading.Lock()
    stop_event = threading.Event()

    # ---- 안전 종료 핸들러 ----
    def _handle_sigint(sig, frame):
        print("\n🛑 SIGINT 감지 → 종료 준비 (답변/재생 마무리 대기)")
        stop_event.set()
    signal.signal(signal.SIGINT, _handle_sigint)

    # ---- 초기화 (모터 모드 설정) ----
    try:
        I.init_pan_tilt_and_extras(port, pkt, dxl_lock)
        I.init_wheels(port, pkt, dxl_lock)
        print("▶ 초기화 완료: 팬/틸트 + 보조(Position), 휠(Velocity)")
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        _graceful_shutdown(port, pkt, dxl_lock)
        sys.exit(1)

    # ---- Face tracker thread ----
    cam_default = str(_default_cam_index())
    cam_index = int(_get_env("CAM_INDEX", cam_default))
    t_face = threading.Thread(
        target=F.face_tracker_worker,
        args=(port, pkt, dxl_lock, stop_event),
        kwargs=dict(camera_index=cam_index, draw_mesh=True, print_debug=True),
        name="face",
        daemon=True,
    )
    t_face.start()
    print(f"▶ FaceTracker 시작 (camera_index={cam_index})")

    # ---- Dance callbacks (PTT에서 호출) ----
    start_dance = lambda: D.start_dance(port, pkt, dxl_lock)
    stop_dance = lambda: D.stop_dance(port, pkt, dxl_lock, return_home=True)

    # ---- PTT thread ----
    t_ptt = threading.Thread(
        target=run_ptt, args=(start_dance, stop_dance), name="ptt", daemon=True
    )
    t_ptt.start()

    # ---- Wheel loop (메인 루프) ----
    try:
        W.wheel_loop(port, pkt, dxl_lock, stop_event)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        # 쓰레드 종료 유도
        stop_event.set()
        try:
            t_face.join(timeout=2.0)
        except Exception:
            pass
        try:
            t_ptt.join(timeout=5.0)
        except Exception:
            pass

        # 안전 종료 루틴
        _graceful_shutdown(port, pkt, dxl_lock)
        print("■ launcher 종료")


if __name__ == "__main__":
    main()
