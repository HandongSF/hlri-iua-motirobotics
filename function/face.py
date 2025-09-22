# function/face.py

from __future__ import annotations
import os
import threading
import platform
import queue
import time
from . import config as C, dxl_io as io, suppress
from dynamixel_sdk import PortHandler, PacketHandler
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

# [수정] shared_state 파라미터를 다시 받도록 수정
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
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    
    if not cap.isOpened():
        print(f"⚠️ 카메라({camera_index}) 열기 실패")
        landmarker.close(); return
    print(f"✅ 카메라({camera_index})가 성공적으로 열렸습니다.")
    
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    last_mode = shared_state.get('mode', 'tracking')

    last_error_pan = 0
    last_error_tilt = 0
    integral_pan = 0
    integral_tilt = 0

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

            # mediapipe 처리를 위해 BGR -> RGB 변환
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            
            frame_timestamp_ms = int(time.perf_counter() * 1000)
            res = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
            
            current_mode = shared_state.get('mode', 'tracking')

            if current_mode != last_mode:
                if current_mode == 'ox_quiz':
                    print("▶ Mode changed to OX_QUIZ: Resetting motor position.")
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

                        error_pan = nx - cx
                        error_tilt = cy - ny

                        if abs(error_pan) > C.DEAD_ZONE or abs(error_tilt) > C.DEAD_ZONE:
                            # 2. 오차 누적 (I Term)
                            integral_pan += error_pan
                            integral_tilt += error_tilt
                            # I 값이 너무 커지는 것을 방지 (Integral Windup 방지)
                            integral_pan = io.clamp(integral_pan, -200, 200)
                            integral_tilt = io.clamp(integral_tilt, -200, 200)

                            # 3. 오차의 변화량 계산 (D Term)
                            derivative_pan = error_pan - last_error_pan
                            derivative_tilt = error_tilt - last_error_tilt
                            
                            # 4. 최종 제어량 계산 = P + I + D
                            pan_delta = (error_pan * C.KP_PAN) + (integral_pan * C.KI_PAN) + (derivative_pan * C.KD_PAN)
                            tilt_delta = (error_tilt * C.KP_TILT) + (integral_tilt * C.KI_TILT) + (derivative_tilt * C.KD_TILT)
                        else:
                            pan_delta, tilt_delta = 0, 0
                            # 목표에 도달하면 I값 초기화
                            integral_pan, integral_tilt = 0, 0

                        # 5. 다음 프레임을 위해 현재 오차를 '이전 오차'로 저장
                        last_error_pan = error_pan
                        last_error_tilt = error_tilt
                        
                        # 6. 최종 위치 업데이트
                        pan_pos  = int(io.clamp(pan_pos  + PAN_SIGN  * pan_delta,  C.SERVO_MIN, C.SERVO_MAX))
                        tilt_pos = int(io.clamp(tilt_pos + TILT_SIGN * tilt_delta, C.SERVO_MIN, C.SERVO_MAX))

                        # --- ✅ PID 제어 로직 끝 ---

                        with lock:
                            io.write4(pkt, port, C.PAN_ID,  C.ADDR_GOAL_POSITION, pan_pos)
                            io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)
                        
                        cv2.circle(frame, (cx, cy), 5, (255, 0, 0), -1)
                        cv2.circle(frame, (nx, ny), 5, (0, 0, 255), -1)
                        cv2.putText(frame, "Mode: Tracking", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                else:
                    cv2.putText(frame, "Mode: Tracking (Sleepy)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (128, 128, 128), 2)
            
            # --- 수정된 부분 시작 ---
            elif current_mode == 'ox_quiz':

                left_count, right_count = 0, 0
                if res.face_landmarks:
                    for face_landmarks in res.face_landmarks:
                        nose_landmark = face_landmarks[1] # 코 위치 기준
                        face_x_position = int(nose_landmark.x * w)
                        if face_x_position < cx:
                            left_count += 1
                        else:
                            right_count += 1

                # 1. 화면 중앙에 흰색 세로선 그리기
                cv2.line(frame, (cx, 0), (cx, h), (255, 255, 255), 3)

                # 2. 왼쪽 상단에 'X' 표시 (빨간색)
                cv2.putText(frame, "X", (40, 80), cv2.FONT_HERSHEY_TRIPLEX, 3, (0, 0, 255), 7)
                cv2.putText(frame, f": {left_count}", (160, 80), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 255), 7)

                # 3. 오른쪽 상단에 'O' 표시 (초록색)
                cv2.putText(frame, "O", (w - 250, 80), cv2.FONT_HERSHEY_TRIPLEX, 3, (0, 255, 0), 7)
                cv2.putText(frame, f": {right_count}", (w - 130, 80), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 255, 0), 7)
                
                # 4. 화면 하단에 총 인원 수 표시
                total_faces = left_count + right_count
                count_text = f"Total: {total_faces}"
                text_size = cv2.getTextSize(count_text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)[0]
                text_x = w - text_size[0] - 20
                text_y = h - 30
                cv2.putText(frame, count_text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
            # --- 수정된 부분 끝 ---

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

# display_loop는 shared_state를 직접 제어하지 않으므로 수정할 필요 없음
def display_loop_main_thread(stop_event: threading.Event, window_name: str = "Auto-Track Face Center"):
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
            if key == 27: # ESC 키로 종료
                stop_event.set(); break
    finally:
        try: cv2.destroyAllWindows()
        except Exception: pass