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
# function/face.py

from __future__ import annotations
import os
import threading
import platform
import queue
import time
from .vision_brain import RobotBrain
from . import config as C, dxl_io as io, suppress
from dynamixel_sdk import PortHandler, PacketHandler
from mediapipe.framework.formats import landmark_pb2
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# [추가] screeninfo 라이브러리를 가져옵니다. 라이브러리가 없어도 오류가 나지 않도록 try-except로 감싸줍니다.
try:
    import screeninfo
except ImportError:
    screeninfo = None

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
                         mouth_event_queue: queue.Queue | None = None,
                         camera_index: int = 1,
                         draw_mesh: bool = True,
                         print_debug: bool = True,
                         brain: RobotBrain = None):

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
            output_face_blendshapes=True,
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
    
    # [추가] 마지막으로 모터에 전송한 위치를 기억하는 변수
    last_sent_pan = pan_pos
    last_sent_tilt = tilt_pos

    if print_debug:
        print(f"▶ Initial(Home) pan={pan_pos}, tilt={tilt_pos}")

    # ============================================================
    #         ↓↓↓ [추가] 얼굴 추적용 가속도 및 속도 설정 ↓↓↓
    # ============================================================
    print(f"🤖 추적 모터(Pan/Tilt)에 가속도 및 속도({C.PROFILE_VELOCITY}) 설정...")
    with lock:
        # 가속도 값을 설정하여 움직임을 부드럽게 만듭니다.
        # 값이 0이면 급출발/급정거, 값이 높을수록 부드럽게 출발/정지합니다.
        accel_value = 30 
        
        io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_VELOCITY, C.PROFILE_VELOCITY)
        io.write4(pkt, port, C.TILT_ID, C.ADDR_PROFILE_VELOCITY, C.PROFILE_VELOCITY)
        
        io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
        io.write4(pkt, port, C.TILT_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
    # ============================================================

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
    debug_counter = 0

    last_mouth_open_time = 0.0
    is_speaking_state = False
    MOUTH_OPEN_THRESHOLD = 0.08   
    SPEAKING_TIMEOUT_SEC = 2.0 
    
    # [FPS 추가] FPS 계산을 위한 이전 시간 초기화
    prev_time = 0

    def get_blendshape_score(blendshape_list, category_name):
        for category in blendshape_list:
            if category.category_name == category_name:
                return category.score
        return 0.0

    last_recog_time = 0
    RECOG_INTERVAL = 1 # 1초마다 인식 시도 (과부하 방지)

    is_initial_recognition_active = True

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok: break

            # [FPS 추가] 시간 측정 및 FPS 계산
            current_time = time.time()
            fps = 0
            if prev_time != 0:
                delta_time = current_time - prev_time
                if delta_time > 0:
                    fps = 1 / delta_time
            prev_time = current_time

            frame = cv2.flip(frame, 1)

            try:
                if not video_frame_q.full():
                    video_frame_q.put_nowait(frame.copy())
            except Exception: pass
            
            current_mode = shared_state.get('mode', 'tracking') # 현재 모드를 여기서 가져옵니다.

            if brain and not sleepy_event.is_set():
                cur_time = time.time()
                
                # 1. 강제 학습 모드인지 확인 (Gemini가 10초간 켜둠)
                force_learning = shared_state.get('force_learning', False)
                target_name = shared_state.get('learning_target_name', None)

                # 2. 초기 인식 상태 업데이트
                # detected_user가 Unknown이 아니거나, 로그인 된 사용자가 있다면 인식 중단
                if shared_state.get('detected_user') not in ["Unknown", None, "Thinking..."] or shared_state.get('current_user_name') is not None:
                    is_initial_recognition_active = False


                # 3. [핵심 수정] RobotBrain 실행 조건
                #   A. 강제 학습 모드일 때 (최우선)
                #   B. tracking 모드이고, 초기 인식이 필요하거나, 인식 주기에 도달했을 때
                
                is_recognition_needed = (
                    force_learning or 
                    (current_mode == 'tracking' and is_initial_recognition_active)
                )

                if is_recognition_needed:
                    
                    last_recog_time = cur_time
                    
                    recog_frame = frame.copy()
                    emb, name = brain.recognize_face(recog_frame) # <-- 고부하 로직 실행
                    
                    # 상태 공유 및 로그
                    if emb is not None:
                        shared_state['current_face_embedding'] = emb
                        
                        # [추가] 초기 인식 기간 동안에는 detected_user를 업데이트하여 PressToTalk의 run() 루프가 로그인 처리하도록 돕습니다.
                        if is_initial_recognition_active and name not in [None, "Unknown", "Thinking..."]:
                            shared_state['detected_user'] = name 
                            
                        # [추가] 일반 인식 시 로그 출력 (디버깅용)
                        if not force_learning and print_debug and name != "Thinking...":
                            print(f"👤 [일반 인식] detected_user: {shared_state.get('detected_user')}, ART Result: {name}")

                    else:
                        shared_state['current_face_embedding'] = None
                        if is_initial_recognition_active:
                            shared_state['detected_user'] = "Unknown"
                        
                    # 4. [핵심] 집중 학습 실행 (강제 학습 모드에서만 실행됨)
                    if force_learning and emb is not None and target_name:
                        # 뇌에 강제 주입 (쿨타임 없음)
                        msg = brain.register_face(emb, target_name)
                        if print_debug:
                            print(f"🔥 [집중 학습 중] {target_name}: {msg}")
                            
                        # 화면에 '학습 중' 표시 (UI)
                        cv2.putText(frame, "SCANNING MODE", (10, 100), 
                                     cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            
            frame_timestamp_ms = int(time.perf_counter() * 1000)
            res = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
            
            # --- 입 모양 감지 로직 ---
            if mouth_event_queue and res.face_blendshapes and res.face_blendshapes[0]:
                bs = res.face_blendshapes[0]
                mouth_open_score = get_blendshape_score(bs, 'jawOpen')
      
                debug_counter += 1
                if debug_counter % 30 == 0: # 로그 출력 빈도 줄임
                    print(f"👄 Mouth Score: {mouth_open_score:.4f}")
                
                is_mouth_currently_open = mouth_open_score > MOUTH_OPEN_THRESHOLD
                current_sys_time = time.time()

                if is_mouth_currently_open:
                    last_mouth_open_time = current_sys_time
                    if not is_speaking_state:
                        print("👄 Mouth open detected, sending START_RECORDING")
                        is_speaking_state = True
                        try:
                            mouth_event_queue.put_nowait("START_RECORDING")
                        except Exception: pass
                else:
                    if is_speaking_state and (current_sys_time - last_mouth_open_time > SPEAKING_TIMEOUT_SEC):
                        print("👄 Mouth closed for 2s, sending STOP_RECORDING")
                        is_speaking_state = False
                        try:
                            mouth_event_queue.put_nowait("STOP_RECORDING")
                        except Exception: pass

            # --- 모드 변경 감지 ---
            current_mode = shared_state.get('mode', 'tracking')

            if current_mode != last_mode:
                if current_mode == 'ox_quiz':
                    print("▶ Mode changed to OX_QUIZ: Resetting motor position.")
                    pan_pos, tilt_pos = home_pan_pos, home_tilt_pos
                    with lock:
                        io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, pan_pos)
                        io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)
                    last_sent_pan, last_sent_tilt = pan_pos, tilt_pos # 위치 리셋 시 동기화
                
                elif current_mode == 'tracking':
                    print("▶ Mode changed to Tracking: Re-reading current motor position.")
                    pan_pos = read_pos(C.PAN_ID)
                    tilt_pos = read_pos(C.TILT_ID)
                    last_sent_pan, last_sent_tilt = pan_pos, tilt_pos # 위치 리셋 시 동기화
                last_mode = current_mode

            # --- 얼굴 추적 로직 ---
            if current_mode == 'tracking':
                if not sleepy_event.is_set():
                    if res.face_landmarks:
                        lm = res.face_landmarks[0][1] # 코 끝 좌표
                        nx, ny = int(lm.x * w), int(lm.y * h)

                        error_pan = nx - cx
                        error_tilt = cy - ny

                        # PID 제어
                        if abs(error_pan) > C.DEAD_ZONE or abs(error_tilt) > C.DEAD_ZONE:
                            integral_pan += error_pan
                            integral_tilt += error_tilt
                            integral_pan = io.clamp(integral_pan, -200, 200)
                            integral_tilt = io.clamp(integral_tilt, -200, 200)
                            derivative_pan = error_pan - last_error_pan
                            derivative_tilt = error_tilt - last_error_tilt
                            
                            pan_delta = (error_pan * C.KP_PAN) + (integral_pan * C.KI_PAN) + (derivative_pan * C.KD_PAN)
                            tilt_delta = (error_tilt * C.KP_TILT) + (integral_tilt * C.KI_TILT) + (derivative_tilt * C.KD_TILT)
                        else:
                            pan_delta, tilt_delta = 0, 0
                            integral_pan, integral_tilt = 0, 0

                        last_error_pan = error_pan
                        last_error_tilt = error_tilt
                        
                        # 목표 위치 계산
                        pan_pos  = int(io.clamp(pan_pos  + C.PAN_SIGN  * pan_delta,  C.SERVO_MIN, C.SERVO_MAX))
                        tilt_pos = int(io.clamp(tilt_pos + C.TILT_SIGN * tilt_delta, C.SERVO_MIN, C.TILT_POS_MAX))

                        # ▼▼▼ [핵심 수정] 미세한 떨림(노이즈) 방지 로직 ▼▼▼
                        # 계산된 목표 위치와 마지막으로 전송한 위치의 차이가 
                        # 최소 임계값(MIN_MOVE_DELTA)보다 클 때만 모터에 명령을 보냅니다.
                        # 이렇게 하면 불필요한 미세 전류 공급을 막아 발열을 줄입니다.
                        move_threshold = getattr(C, 'MIN_MOVE_DELTA', 5) # config.py에 정의된 값 사용 (기본 5)
                        
                        should_move_pan = abs(pan_pos - last_sent_pan) > move_threshold
                        should_move_tilt = abs(tilt_pos - last_sent_tilt) > move_threshold

                        if should_move_pan or should_move_tilt:
                            with lock:
                                if should_move_pan:
                                    io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, pan_pos)
                                    last_sent_pan = pan_pos # 전송한 위치 업데이트
                                
                                if should_move_tilt:
                                    io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)
                                    last_sent_tilt = tilt_pos # 전송한 위치 업데이트
                        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

                        cv2.circle(frame, (cx, cy), 5, (255, 0, 0), -1)
                        cv2.circle(frame, (nx, ny), 5, (0, 0, 255), -1)
                        cv2.putText(frame, "Mode: Tracking", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                else:
                    cv2.putText(frame, "Mode: Tracking (Sleepy)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (128, 128, 128), 2)
            
            elif current_mode == 'ox_quiz':
                # (OX 퀴즈 그래픽 그리기 로직은 기존과 동일)
                left_count, right_count = 0, 0
                if res.face_landmarks:
                    for face_landmarks in res.face_landmarks:
                        nose_landmark = face_landmarks[1]
                        face_x_position = int(nose_landmark.x * w)
                        if face_x_position < cx:
                            left_count += 1
                        else:
                            right_count += 1

                cv2.line(frame, (cx, 0), (cx, h), (255, 255, 255), 3)
                cv2.putText(frame, "X", (40, 80), cv2.FONT_HERSHEY_TRIPLEX, 3, (0, 0, 255), 7)
                cv2.putText(frame, f": {left_count}", (160, 80), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 255), 7)
                cv2.putText(frame, "O", (w - 250, 80), cv2.FONT_HERSHEY_TRIPLEX, 3, (0, 255, 0), 7)
                cv2.putText(frame, f": {right_count}", (w - 130, 80), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 255, 0), 7)
                
                total_faces = left_count + right_count
                count_text = f"Total: {total_faces}"
                text_size = cv2.getTextSize(count_text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)[0]
                text_x = w - text_size[0] - 20
                text_y = h - 30
                cv2.putText(frame, count_text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)

            if draw_mesh and res.face_landmarks:
                for landmark_list in res.face_landmarks:
                    x_min = min([landmark.x for landmark in landmark_list])
                    y_min = min([landmark.y for landmark in landmark_list])
                    x_max = max([landmark.x for landmark in landmark_list])
                    y_max = max([landmark.y for landmark in landmark_list])
                    start_point = (int(x_min * w), int(y_min * h))
                    end_point = (int(x_max * w), int(y_max * h))
                    cv2.rectangle(frame, start_point, end_point, (0, 255, 0), 2)
                    
            user_name = shared_state.get('detected_user', 'Unknown')
            _publish_frame(frame)

    finally:
        # ============================================================
        #         ↓↓↓ [추가] 모터 설정을 기본값으로 초기화 ↓↓↓
        # ============================================================
        print(f"🤖 추적 모터(Pan/Tilt) 설정 초기화 (가속도 0, 속도 100)...")
        try:
            with lock:
                default_velocity = 100 # init.py의 기본 속도
                io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_VELOCITY, default_velocity)
                io.write4(pkt, port, C.TILT_ID, C.ADDR_PROFILE_VELOCITY, default_velocity)
                
                io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_ACCELERATION, 0) # 가속도 0 (기본값)
                io.write4(pkt, port, C.TILT_ID, C.ADDR_PROFILE_ACCELERATION, 0) # 가속도 0 (기본값)
        except Exception as e:
            print(f"⚠️  추적 모터 설정 초기화 중 오류: {e}")
        # ============================================================
        
        try: cap.release()
        except Exception: pass
        landmarker.close()

def display_loop_main_thread(stop_event: threading.Event, window_name: str = "Auto-Track Face Center"):
    cv2, _ = suppress.import_cv2_mp()

    if not _can_show_window_in_this_thread():
        print("⚠️ display_loop_main_thread는 반드시 메인 스레드에서 호출해야 합니다.")
        return
    try:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        MONITOR_INDEX_FOR_TRIPLE_SETUP = 2

        x_pos, y_pos = 0, 0

        if screeninfo:
            try:
                monitors = screeninfo.get_monitors()
                num_monitors = len(monitors)
                
                target_index = 0

                if num_monitors >= 3:
                    target_index = MONITOR_INDEX_FOR_TRIPLE_SETUP
                    print(f"✅ 카메라: 모니터 {num_monitors}개 감지 -> #{target_index}에 배치 시도")
                else:
                    target_index = 0
                    print(f"✅ 카메라: 모니터 {num_monitors}개 감지 -> 주 모니터(#{target_index})에 배치")

                if num_monitors > target_index:
                    target_monitor = monitors[target_index]
                else:
                    target_monitor = monitors[0]
                    print(f"⚠️ 지정된 카메라 모니터 #{target_index}를 찾을 수 없음")
                
                camera_width = 1280
                x_pos = target_monitor.x + (target_monitor.width - camera_width) // 2
                y_pos = target_monitor.y

            except Exception as e:
                print(f"❌ 카메라 모니터 확인 오류: {e}")
        else:
            print("⚠️ 'screeninfo' 라이브러리가 없어 카메라를 주 모니터에 배치합니다.")

        cv2.moveWindow(window_name, x_pos, y_pos)
        
        print(f"✅ 카메라 창을 좌표 ({x_pos}, {y_pos})에 배치합니다.")
        
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