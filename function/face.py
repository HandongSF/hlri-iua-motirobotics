# mk2/face.py
from __future__ import annotations
import os
import threading
import platform
import queue
import time
from . import config as C, dxl_io as io, suppress
from dynamixel_sdk import PortHandler, PacketHandler
# landmark_pb2와 drawing_utils는 이제 직접 사용하지 않으므로 import 순서를 조정하거나 그대로 두어도 무방합니다.
from mediapipe.framework.formats import landmark_pb2

from mediapipe.tasks import python
from mediapipe.tasks.python import vision

_IS_DARWIN = (platform.system() == "Darwin")

PAN_SIGN  = int(os.getenv("PAN_SIGN",  "1"))
TILT_SIGN = int(os.getenv("TILT_SIGN", "-1"))

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
    return not (_IS_DARWIN and threading.current_thread() is not threading.main_thread())

def face_tracker_worker(port: PortHandler, pkt: PacketHandler, lock: threading.Lock,
                        stop_event: threading.Event, video_frame_q: queue.Queue,
                        sleepy_event: threading.Event,
                        shared_state: dict,
                        camera_index: int = 1,
                        draw_mesh: bool = True,
                        print_debug: bool = True):

    cv2, mp = suppress.import_cv2_mp()

    model_asset_path = 'models/face_landmarker.task'

    try:
        base_options = python.BaseOptions(model_asset_path=model_asset_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            num_faces=20,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False
        )
        landmarker = vision.FaceLandmarker.create_from_options(options)
        print("✅ 최신 FaceLandmarker 모델 로딩 완료.")

    except Exception as e:
        print(f"❌ FaceLandmarker 모델 로딩 실패: {e}")
        return

    def read_pos(dxl_id: int) -> int:
        v = io.read_present_position(pkt, port, lock, dxl_id)
        v = _as_int(v, None)
        if v is None:
            v = (C.SERVO_MIN + C.SERVO_MAX) // 2
        return v

    home_pan_pos = read_pos(C.PAN_ID)
    home_tilt_pos = read_pos(C.TILT_ID)
    pan_pos  = home_pan_pos
    tilt_pos = home_tilt_pos
    if print_debug:
        print(f"▶ Initial(Home) pan={pan_pos}, tilt={tilt_pos}")

    print(f"▶ 카메라({camera_index})를 여는 중입니다...")
    # [수정 1] Windows에서 안정적인 카메라 연결을 위해 CAP_DSHOW 사용
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    
    if not cap.isOpened():
        print(f"⚠️ 카메라({camera_index}) 열기 실패")
        landmarker.close(); return
    print(f"✅ 카메라({camera_index})가 성공적으로 열렸습니다.")
    
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    last_mode = shared_state.get('mode', 'tracking')

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok: break

            frame = cv2.flip(frame, 1)

            try:
                if not video_frame_q.full():
                    video_frame_q.put_nowait(frame.copy())
            except Exception: pass
            
            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            
            # [수정 2] 불안정한 카메라 시간 대신 시스템 시간으로 안정적인 타임스탬프 생성
            frame_timestamp_ms = int(time.perf_counter() * 1000)
            res = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
            
            current_mode = shared_state.get('mode', 'tracking')

            if current_mode != last_mode:
                if current_mode == 'counting':
                    print("▶ Mode changed to Counting: Resetting motor position.")
                    pan_pos, tilt_pos = home_pan_pos, home_tilt_pos
                    with lock:
                        io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, pan_pos)
                        io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)
                
                elif current_mode == 'tracking':
                    print("▶ Mode changed to Tracking: Re-reading current motor position.")
                    pan_pos = read_pos(C.PAN_ID)
                    tilt_pos = read_pos(C.TILT_ID)
            last_mode = current_mode

            if current_mode == 'tracking':
                if not sleepy_event.is_set():
                    if res.face_landmarks:
                        lm = res.face_landmarks[0][1]
                        nx, ny = int(lm.x * w), int(lm.y * h)
                        off_x = int(io.clamp(nx - cx, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))
                        off_y = int(io.clamp(cy - ny, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))
                        pan_delta  = 0 if abs(off_x) < C.DEAD_ZONE else max(1, int(abs(off_x * C.KP_PAN)))  * (1 if off_x > 0 else -1)
                        tilt_delta = 0 if abs(off_y) < C.DEAD_ZONE else max(1, int(abs(off_y * C.KP_TILT))) * (1 if off_y > 0 else -1)
                        pan_pos  = int(io.clamp(pan_pos  + PAN_SIGN  * pan_delta,  C.SERVO_MIN, C.SERVO_MAX))
                        tilt_pos = int(io.clamp(tilt_pos + TILT_SIGN * tilt_delta, C.SERVO_MIN, C.SERVO_MAX))
                        with lock:
                            io.write4(pkt, port, C.PAN_ID,  C.ADDR_GOAL_POSITION, pan_pos)
                            io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)
                        cv2.circle(frame, (cx, cy), 5, (255, 0, 0), -1)
                        cv2.circle(frame, (nx, ny), 5, (0, 0, 255), -1)
                        cv2.putText(frame, "Mode: Tracking", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                else:
                    cv2.putText(frame, "Mode: Tracking (Sleepy)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (128, 128, 128), 2)

            elif current_mode == 'counting':
                left_count, right_count = 0, 0
                if res.face_landmarks:
                    for face_landmark_list in res.face_landmarks:
                        lm = face_landmark_list[1]
                        nx = int(lm.x * w)
                        if nx < cx: left_count += 1
                        else: right_count += 1
                
                total_count = left_count + right_count
                
                cv2.line(frame, (cx, 0), (cx, h), (0, 255, 255), 2)
                cv2.putText(frame, f"Left: {left_count}", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 0), 3)
                cv2.putText(frame, f"Right: {right_count}", (w - 220, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 0), 3)
                
                total_text = f"Total: {total_count}"
                (text_w, text_h), _ = cv2.getTextSize(total_text, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3)
                text_pos_x = w - text_w - 20
                text_pos_y = h - 30
                cv2.putText(frame, total_text, (text_pos_x, text_pos_y), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
                cv2.putText(frame, "Mode: Counting", (10, h-30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)

            if draw_mesh and res.face_landmarks:
                for landmark_list in res.face_landmarks:
                    x_min = min([landmark.x for landmark in landmark_list])
                    y_min = min([landmark.y for landmark in landmark_list])
                    x_max = max([landmark.x for landmark in landmark_list])
                    y_max = max([landmark.y for landmark in landmark_list])
                    start_point = (int(x_min * w), int(y_min * h))
                    end_point = (int(x_max * w), int(y_max * h))
                    cv2.rectangle(frame, start_point, end_point, (0, 255, 0), 2)
            
            _publish_frame(frame)

    finally:
        try: cap.release()
        except Exception: pass
        landmarker.close()

def display_loop_main_thread(stop_event: threading.Event, shared_state: dict, window_name: str = "Auto-Track Face Center"):
    # 이 함수는 변경사항이 없습니다.
    cv2, _ = suppress.import_cv2_mp()
    if not _can_show_window_in_this_thread():
        print("⚠️ display_loop_main_thread는 반드시 메인 스레드에서 호출해야 합니다.")
        return
    try:
        while not stop_event.is_set():
            try:
                frame = _DISPLAY_Q.get(timeout=0.05)
            except queue.Empty:
                continue
            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                stop_event.set(); break
            elif key == ord('m'):
                if shared_state.get('mode') == 'tracking':
                    shared_state['mode'] = 'counting'
                    print("✅ Mode changed to: [Counting]")
                else:
                    shared_state['mode'] = 'tracking'
                    print("✅ Mode changed to: [Tracking]")
    finally:
        try: cv2.destroyAllWindows()
        except Exception: pass