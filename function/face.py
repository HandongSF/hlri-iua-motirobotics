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
    
    last_sent_pan = pan_pos
    last_sent_tilt = tilt_pos

    if print_debug:
        print(f"▶ Initial(Home) pan={pan_pos}, tilt={tilt_pos}")

    # ============================================================
    #         ↓↓↓ [설정] 실시간 추적을 위해 가속도 끄기 ↓↓↓
    # ============================================================
    print(f"🤖 추적 모터(Pan/Tilt) 설정 (반응속도 최우선)...")
    with lock:
        accel_value = 0 
        velocity_value = 0 
        
        io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_VELOCITY, velocity_value)
        io.write4(pkt, port, C.TILT_ID, C.ADDR_PROFILE_VELOCITY, velocity_value)
        
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
    
    prev_time = 0

    # ============================================================
    #      ↓↓↓ [설정] 좌표 부드럽게 만들기 (스무딩 변수) ↓↓↓
    # ============================================================
    smooth_nx = 1280 // 2
    smooth_ny = 720 // 2
    SMOOTH_FACTOR = 0.4 
    # ============================================================

    def get_blendshape_score(blendshape_list, category_name):
        for category in blendshape_list:
            if category.category_name == category_name:
                return category.score
        return 0.0

    last_recog_time = 0
    
    # ▼▼▼ [수정] 인식 주기를 0.5초로 단축하여 반응성 향상 ▼▼▼
    RECOG_INTERVAL = 0.5 
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    is_initial_recognition_active = True

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok: break

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
            
            current_mode = shared_state.get('mode', 'tracking')

            if brain and not sleepy_event.is_set():
                cur_time = time.time() # 현재 시간
                
                force_learning = shared_state.get('force_learning', False)
                target_name = shared_state.get('learning_target_name', None)

                if shared_state.get('detected_user') not in ["Unknown", None, "Thinking..."] or shared_state.get('current_user_name') is not None:
                    is_initial_recognition_active = False

                is_recognition_needed = (
                    force_learning or 
                    (current_mode == 'tracking' and is_initial_recognition_active)
                )

                if is_recognition_needed and (cur_time - last_recog_time >= RECOG_INTERVAL):
                    
                    last_recog_time = cur_time # 마지막 실행 시간 갱신
                    
                    recog_frame = frame.copy()
                    emb, name = brain.recognize_face(recog_frame)
                    
                    if emb is not None:
                        # 얼굴이 감지됨
                        shared_state['current_face_embedding'] = emb
                        
                        if is_initial_recognition_active:
                            # ▼▼▼ [중요 수정] Unknown이어도 업데이트해야 gemini_api가 반응함! ▼▼▼
                            if name not in [None, "Thinking..."]:
                                shared_state['detected_user'] = name 
                            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                        
                        # (로그 출력)
                        if not force_learning and print_debug and name != "Thinking...":
                            print(f"👤 [ART 인식] detected_user: {shared_state.get('detected_user')}, 결과: {name}")
                    else:
                        # 얼굴이 감지되지 않음 (벽, 허공 등)
                        shared_state['current_face_embedding'] = None
                        if is_initial_recognition_active:
                            # ▼▼▼ [수정] 아무도 없으면 None으로 설정 (유령 인식 방지) ▼▼▼
                            shared_state['detected_user'] = None
                            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                        
                    if force_learning and emb is not None and target_name:
                        msg = brain.register_face(emb, target_name)
                        if print_debug:
                            print(f"🔥 [집중 학습 중] {target_name}: {msg}")
                        cv2.putText(frame, "SCANNING MODE", (10, 100), 
                                     cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            frame_timestamp_ms = int(time.perf_counter() * 1000)
            res = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
            
            if mouth_event_queue and res.face_blendshapes and res.face_blendshapes[0]:
                # (기존 입모양 인식 로직 유지)
                bs = res.face_blendshapes[0]
                mouth_open_score = get_blendshape_score(bs, 'jawOpen')
                debug_counter += 1
                if debug_counter % 30 == 0: 
                    print(f"👄 Mouth Score: {mouth_open_score:.4f}")
                
                is_mouth_currently_open = mouth_open_score > MOUTH_OPEN_THRESHOLD
                current_sys_time = time.time()

                if is_mouth_currently_open:
                    last_mouth_open_time = current_sys_time
                    if not is_speaking_state:
                        print("👄 Mouth open detected, sending START_RECORDING")
                        is_speaking_state = True
                        try: mouth_event_queue.put_nowait("START_RECORDING")
                        except Exception: pass
                else:
                    if is_speaking_state and (current_sys_time - last_mouth_open_time > SPEAKING_TIMEOUT_SEC):
                        print("👄 Mouth closed for 2s, sending STOP_RECORDING")
                        is_speaking_state = False
                        try: mouth_event_queue.put_nowait("STOP_RECORDING")
                        except Exception: pass

            current_mode = shared_state.get('mode', 'tracking')

            if current_mode != last_mode:
                # (기존 모드 변경 로직 유지)
                if current_mode == 'ox_quiz':
                    print("▶ Mode changed to OX_QUIZ: Resetting motor position.")
                    pan_pos, tilt_pos = home_pan_pos, home_tilt_pos
                    with lock:
                        io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, pan_pos)
                        io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)
                    last_sent_pan, last_sent_tilt = pan_pos, tilt_pos
                
                elif current_mode == 'tracking':
                    print("▶ Mode changed to Tracking: Re-reading current motor position.")
                    pan_pos = read_pos(C.PAN_ID)
                    tilt_pos = read_pos(C.TILT_ID)
                    last_sent_pan, last_sent_tilt = pan_pos, tilt_pos
                last_mode = current_mode

            if current_mode == 'tracking':
                if not sleepy_event.is_set():
                    if res.face_landmarks:
                        lm = res.face_landmarks[0][1] # 코 끝 좌표
                        raw_nx, raw_ny = int(lm.x * w), int(lm.y * h)

                        # ============================================================
                        #        ↓↓↓ [설정] 좌표 스무딩 적용 ↓↓↓
                        # ============================================================
                        smooth_nx = int(raw_nx * SMOOTH_FACTOR + smooth_nx * (1 - SMOOTH_FACTOR))
                        smooth_ny = int(raw_ny * SMOOTH_FACTOR + smooth_ny * (1 - SMOOTH_FACTOR))
                        
                        nx, ny = smooth_nx, smooth_ny 
                        # ============================================================

                        error_pan = nx - cx
                        error_tilt = cy - ny

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
                        
                        pan_pos  = int(io.clamp(pan_pos  + C.PAN_SIGN  * pan_delta,  C.SERVO_MIN, C.SERVO_MAX))
                        tilt_pos = int(io.clamp(tilt_pos + C.TILT_SIGN * tilt_delta, C.SERVO_MIN, C.TILT_POS_MAX))

                        # ============================================================
                        #        ↓↓↓ [설정] 최소 이동 임계값 조정 ↓↓↓
                        # ============================================================
                        move_threshold = 1 
                        
                        should_move_pan = abs(pan_pos - last_sent_pan) > move_threshold
                        should_move_tilt = abs(tilt_pos - last_sent_tilt) > move_threshold

                        if should_move_pan or should_move_tilt:
                            with lock:
                                if should_move_pan:
                                    io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, pan_pos)
                                    last_sent_pan = pan_pos
                                
                                if should_move_tilt:
                                    io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)
                                    last_sent_tilt = tilt_pos
                        # ============================================================

                        cv2.circle(frame, (cx, cy), 5, (255, 0, 0), -1)
                        cv2.circle(frame, (nx, ny), 5, (0, 0, 255), -1) # 부드러운 좌표 표시
                        # 원본 좌표도 옅게 표시 (디버깅용)
                        cv2.circle(frame, (raw_nx, raw_ny), 3, (0, 255, 255), -1) 
                        cv2.putText(frame, "Mode: Tracking (Smooth)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                else:
                    cv2.putText(frame, "Mode: Tracking (Sleepy)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (128, 128, 128), 2)
            
            elif current_mode == 'ox_quiz':
                # (OX 퀴즈 로직 유지)
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
                default_velocity = 100 
                io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_VELOCITY, default_velocity)
                io.write4(pkt, port, C.TILT_ID, C.ADDR_PROFILE_VELOCITY, default_velocity)
                
                io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_ACCELERATION, 0) 
                io.write4(pkt, port, C.TILT_ID, C.ADDR_PROFILE_ACCELERATION, 0) 
        except Exception as e:
            print(f"⚠️  추적 모터 설정 초기화 중 오류: {e}")
        
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