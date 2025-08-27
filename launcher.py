# lunch.py
import os, time, signal, threading
from dynamixel_sdk import PortHandler, PacketHandler
from pynput.keyboard import Controller, Key

from function import config as C, init as I, face as F, wheel as W, dance as D, dxl_io as IO
from gemini_api import PressToTalk  # Space=녹음, ESC=종료, "춤"/"그만" 콜백

def run_ptt(start_dance_cb, stop_dance_cb):
    app = PressToTalk(start_dance_cb=start_dance_cb, stop_dance_cb=stop_dance_cb)
    app.run()
    print("■ PTT thread 종료")

def main():
    print("▶ launcher: ONE-PORT launcher (FaceTrack + Wheels + Gemini PTT + Dance)")
    print(f" - Port={C.DEVICENAME}, Baud={C.BAUDRATE}, Proto={C.PROTOCOL_VERSION}")
    print(" - Controls: W/A/S/D=wheel, Space=PTT, ESC/Q=exit, Keywords: '춤'/'그만'")

    port = PortHandler(C.DEVICENAME)
    pkt  = PacketHandler(C.PROTOCOL_VERSION)
    if not port.openPort():
        print("⚠️ 포트 열기 실패"); return
    if not port.setBaudRate(C.BAUDRATE):
        print("⚠️ 보드레이트 설정 실패"); port.closePort(); return

    dxl_lock   = threading.Lock()
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    # 초기화: 팬/틸트(+5,6) / 휠
    I.init_pan_tilt_and_extras(port, pkt, dxl_lock)
    I.init_wheels(port, pkt, dxl_lock)

    # 얼굴추적 스레드
    t_face = threading.Thread(
        target=F.face_tracker_worker,
        args=(port, pkt, dxl_lock, stop_event),
        kwargs=dict(camera_index=int(os.getenv("CAM_INDEX", "1")), draw_mesh=True, print_debug=True),
        name="face", daemon=True
    )
    t_face.start()

    # PTT 콜백
    start_dance = lambda: D.start_dance(port, pkt, dxl_lock)
    stop_dance  = lambda: D.stop_dance(port, pkt, dxl_lock, return_home=True)

    # PTT 스레드
    t_ptt = threading.Thread(target=run_ptt, args=(start_dance, stop_dance), name="ptt", daemon=True)
    t_ptt.start()

    # 휠 루프(메인)
    try:
        W.wheel_loop(port, pkt, dxl_lock, stop_event)
    finally:
        stop_event.set()
        # 댄스 중이면 정지/원위치
        try: stop_dance()
        except Exception: pass

        # PTT 살아있으면 ESC 주입해 종료 유도
        try:
            if t_ptt.is_alive():
                kb = Controller()
                for _ in range(2):
                    kb.press(Key.esc); kb.release(Key.esc); time.sleep(0.3)
        except Exception:
            pass

        t_face.join(timeout=1.0)
        t_ptt.join(timeout=5.0)

        # 팬/틸트 & 보조 포지션 모터 토크 OFF
        with dxl_lock:
            for i in (C.PAN_ID, C.TILT_ID, *C.EXTRA_POS_IDS):
                IO.write1(pkt, port, i, C.ADDR_TORQUE_ENABLE, 0)

        port.closePort()
        print("■ mk2_2 종료 (포트 닫힘)")

if __name__ == "__main__":
    main()
