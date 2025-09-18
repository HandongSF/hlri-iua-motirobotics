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

# mk2/face.py
from __future__ import annotations
import os
import threading
import platform
import queue
from . import config as C, dxl_io as io, suppress
from dynamixel_sdk import PortHandler, PacketHandler

# ▼▼▼▼▼ MediaPipe의 최신 Tasks API를 사용하기 위해 추가 ▼▼▼▼▼
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

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
                        stop_event: threading.Event, video_frame_q: queue.Queue,
                        sleepy_event: threading.Event,
                        camera_index: int = 1,
                        draw_mesh: bool = True,
                        print_debug: bool = True):

    cv2, mp = suppress.import_cv2_mp()

    # ▼▼▼▼▼ Face Mesh를 최신 FaceLandmarker API로 변경 ▼▼▼▼▼
    # 모델 파일 경로 설정 (MediaPipe가 이제 모델을 자동으로 다운로드/관리하므로 파일 경로 대신 이름만 사용)
    model_asset_path = 'models/face_landmarker.task' # 경로를 models/로 지정

    try:
        # FaceLandmarker 옵션 설정
        base_options = python.BaseOptions(model_asset_path=model_asset_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO, # 비디오 스트림 처리에 최적화
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False
        )
        # FaceLandmarker 객체 생성
        landmarker = vision.FaceLandmarker.create_from_options(options)
        print("✅ 최신 FaceLandmarker 모델 로딩 완료.")

    except Exception as e:
        print(f"❌ FaceLandmarker 모델 로딩 실패: {e}")
        return
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

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

    draw_utils   = mp.solutions.drawing_utils
    drawing_spec = draw_utils.DrawingSpec(color=(0,255,0), thickness=1, circle_radius=1)

    cap = cv2.VideoCapture(camera_index, cv2.CAP_ANY)
    if not cap.isOpened():
        print(f"⚠️ 카메라({camera_index}) 열기 실패")
        landmarker.close(); return

    worker_can_show = _can_show_window_in_this_thread()
    draw_in_worker  = (draw_mesh and worker_can_show)
    
    frame_timestamp_ms = 0

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok: break

            try:
                if not video_frame_q.full():
                    video_frame_q.put_nowait(frame.copy())
            except Exception:
                pass

            if not sleepy_event.is_set():
                h, w = frame.shape[:2]
                cx, cy = w // 2, h // 2

                # ▼▼▼▼▼ 이미지 처리 및 랜드마크 감지 로직 변경 ▼▼▼▼▼
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                
                frame_timestamp_ms = int(cap.get(cv2.CAP_PROP_POS_MSEC))
                res = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
                
                if res.face_landmarks:
                    # 첫 번째 얼굴의 코끝(landmark[1]) 좌표를 가져옵니다.
                    lm = res.face_landmarks[0][1]
                    nx, ny = int(lm.x * w), int(lm.y * h)
                # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

                    off_x = int(io.clamp(nx - cx, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))
                    off_y = int(io.clamp(cy - ny, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))

                    pan_delta  = 0 if abs(off_x) < C.DEAD_ZONE else max(1, int(abs(off_x * C.KP_PAN)))  * (1 if off_x > 0 else -1)
                    tilt_delta = 0 if abs(off_y) < C.DEAD_ZONE else max(1, int(abs(off_y * C.KP_TILT))) * (1 if off_y > 0 else -1)

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
                        # ▼▼▼▼▼ 랜드마크 그리기 로직 변경 ▼▼▼▼▼
                        for face_landmark_list in res.face_landmarks:
                            draw_utils.draw_landmarks(
                                image=frame,
                                landmark_list=face_landmark_list,
                                connections=mp.solutions.face_mesh.FACEMESH_TESSELATION,
                                landmark_drawing_spec=drawing_spec,
                                connection_drawing_spec=drawing_spec)
                        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

            _publish_frame(frame)

    finally:
        try: cap.release()
        except Exception: pass
        
        landmarker.close() # landmarker 객체를 닫아줍니다.

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
                continue
            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                stop_event.set(); break
    finally:
        try: cv2.destroyAllWindows()
        except Exception: pass