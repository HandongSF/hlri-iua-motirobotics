# mk2/face.py
from __future__ import annotations
import os
import threading
import platform
import queue
from . import config as C, dxl_io as io, suppress
from dynamixel_sdk import PortHandler, PacketHandler

_IS_DARWIN = (platform.system() == "Darwin")

# 추적 방향 부호 (기본값 -1 = 기존 pan_pos -= delta 와 동일)
PAN_SIGN  = int(os.getenv("PAN_SIGN",  "-1"))
TILT_SIGN = int(os.getenv("TILT_SIGN", "-1"))

# 메인스레드 렌더용 프레임 버스 (마지막 프레임만 유지)
_DISPLAY_Q: "queue.Queue" = queue.Queue(maxsize=1)

def _publish_frame(frame):
    try:
        if _DISPLAY_Q.full():
            try: _DISPLAY_Q.get_nowait()
            except Exception: pass
        _DISPLAY_Q.put_nowait(frame)
    except Exception:
        pass

def _as_int(v, default=None):
    try:
        if isinstance(v, (tuple, list)):
            v = v[0]
        return int(v)
    except Exception:
        return default

def _can_show_window_in_this_thread() -> bool:
    # macOS는 메인스레드만 HighGUI 허용
    return not (_IS_DARWIN and threading.current_thread() is not threading.main_thread())

def face_tracker_worker(port: PortHandler, pkt: PacketHandler, lock: threading.Lock,
                        stop_event: threading.Event,
                        camera_index: int = 1,
                        draw_mesh: bool = True,
                        print_debug: bool = True):
    cv2, mp = suppress.import_cv2_mp()

    def read_pos(dxl_id: int) -> int:
        v = io.read_present_position(pkt, port, lock, dxl_id)
        v = _as_int(v, None)
        if v is None:
            v = (C.SERVO_MIN + C.SERVO_MAX) // 2
        return v

    pan_pos  = read_pos(C.PAN_ID)
    tilt_pos = read_pos(C.TILT_ID)
    if print_debug:
        print(f"▶ Initial pan={pan_pos}, tilt={tilt_pos}")

    mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False, max_num_faces=1, refine_landmarks=True,
        min_detection_confidence=0.5, min_tracking_confidence=0.5
    )
    draw_utils   = mp.solutions.drawing_utils
    drawing_spec = draw_utils.DrawingSpec(color=(0,255,0), thickness=1, circle_radius=1)

    cap = cv2.VideoCapture(camera_index, cv2.CAP_ANY)
    if not cap.isOpened():
        print(f"⚠️ 카메라({camera_index}) 열기 실패")
        mesh.close(); return

    # 맥 워커 스레드에서는 창 표시 금지(메인스레드가 따로 띄움)
    worker_can_show = _can_show_window_in_this_thread()
    draw_in_worker  = (draw_mesh and worker_can_show)

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok: break

            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = mesh.process(rgb)

            if res.multi_face_landmarks:
                lm = res.multi_face_landmarks[0].landmark[1]  # nose tip
                nx, ny = int(lm.x * w), int(lm.y * h)

                off_x = int(io.clamp(nx - cx, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))
                off_y = int(io.clamp(cy - ny, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))  # 화면 위=+

                # 최소 1틱 보장, 데드존 제외
                pan_delta  = 0 if abs(off_x) < C.DEAD_ZONE else max(1, int(abs(off_x * C.KP_PAN)))   * (1 if off_x > 0 else -1)
                tilt_delta = 0 if abs(off_y) < C.DEAD_ZONE else max(1, int(abs(off_y * C.KP_TILT))) * (1 if off_y > 0 else -1)

                # 방향 부호 적용 (기본 -1 = 기존 코드와 동일)
                pan_pos  = int(io.clamp(pan_pos  + PAN_SIGN  * pan_delta,  C.SERVO_MIN, C.SERVO_MAX))
                tilt_pos = int(io.clamp(tilt_pos + TILT_SIGN * tilt_delta, C.SERVO_MIN, C.SERVO_MAX))

                with lock:
                    io.write4(pkt, port, C.PAN_ID,  C.ADDR_GOAL_POSITION, pan_pos)
                    io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)

                # 시각화
                cv2.circle(frame, (cx, cy), 5, (255, 0, 0), -1)
                cv2.circle(frame, (nx, ny), 5, (0, 0, 255), -1)
                cv2.putText(frame, f"Off X:{off_x:+d} Y:{off_y:+d}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,255,255), 2)
                if draw_mesh:
                    draw_utils.draw_landmarks(
                        frame, res.multi_face_landmarks[0],
                        mp.solutions.face_mesh.FACEMESH_TESSELATION, drawing_spec, drawing_spec
                    )

            # --- 표시 ---
            if _IS_DARWIN:
                # 맥: 메인스레드에 프레임 전달만
                _publish_frame(frame)
            else:
                # 윈/리눅스: 워커에서 그대로 표시(기존 기능 유지)
                if draw_in_worker:
                    cv2.imshow("Auto-Track Face Center", frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == 27:
                        stop_event.set(); break

    finally:
        try: cap.release()
        except Exception: pass
        try:
            if (not _IS_DARWIN) and draw_in_worker:
                cv2.destroyAllWindows()
        except Exception:
            pass
        mesh.close()

def display_loop_main_thread(stop_event: threading.Event, window_name: str = "Auto-Track Face Center"):
    """
    macOS 전용: 메인 스레드에서 프레임 버스를 소비하며 imshow/waitKey 실행.
    ESC(27)로 stop_event 세팅.
    """
    cv2, _ = suppress.import_cv2_mp()
    if not _can_show_window_in_this_thread():
        print("⚠️ display_loop_main_thread는 반드시 메인 스레드에서 호출해야 합니다.")
        return
    try:
        while not stop_event.is_set():
            try:
                frame = _DISPLAY_Q.get(timeout=0.05)
            except queue.Empty:
                # 프레임이 잠깐 없을 수 있음
                continue
            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                stop_event.set(); break
    finally:
        try: cv2.destroyAllWindows()
        except Exception: pass
