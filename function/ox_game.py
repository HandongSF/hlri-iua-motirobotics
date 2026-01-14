# function/ox_game.py

import cv2
import mediapipe as mp
import time
import queue
import threading
import os
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

class OxQuizGame:
    """
    1:1 인터랙티브 OX 퀴즈 & VAD(입모양 인식) 워커
    """
    def __init__(self, command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
        self.command_q = command_q
        self.result_q = result_q
        self.video_frame_q = video_frame_q
        self.stop_event = threading.Event()
        
        # 게임 상태
        self.current_score = 0
        self.difficulty = "NORMAL"

        # VAD 설정 (face.py와 동일하게 맞춤)
        self.MOUTH_OPEN_THRESHOLD = 0.08    # 입 벌림 기준값
        self.SPEAKING_TIMEOUT_SEC = 2.0     # 입 다물고 대기하는 시간 (말 끝남 판단)
        
        # 모델 로딩
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        model_path = os.path.join(project_root, 'models/face_landmarker.task')

        try:
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.IMAGE,
                num_faces=1,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
                output_face_blendshapes=True
            )
            self.landmarker = vision.FaceLandmarker.create_from_options(options)
            print("✅ OX퀴즈: 모델 로딩 완료.")
        except Exception as e:
            print(f"❌ OX퀴즈 모델 로딩 실패: {e}")
            self.landmarker = None
            self.stop_event.set()

    def _get_blendshape_score(self, blendshape_list, category_name):
        if not blendshape_list: return 0.0
        for category in blendshape_list:
            if category.category_name == category_name:
                return category.score
        return 0.0

    def _draw_ui(self, frame, msg_top, msg_center=None, timer_ratio=0.0):
        """공통 UI 그리기"""
        h, w = frame.shape[:2]
        
        # 상단 정보바
        bar_color = (255, 100, 0)
        if self.difficulty == "HARD": bar_color = (0, 0, 255)
        elif self.difficulty == "CRAZY": bar_color = (128, 0, 128)

        cv2.rectangle(frame, (0, 0), (w, 60), bar_color, -1)
        info = f"Lv: {self.difficulty} | Score: {self.current_score}"
        cv2.putText(frame, info, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        
        # 메시지 (Top)
        if msg_top:
            cv2.putText(frame, msg_top, (w//2 - 100, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)

        # 중앙 메시지 박스
        if msg_center:
            text_size = cv2.getTextSize(msg_center, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)[0]
            tx = (w - text_size[0]) // 2
            ty = h // 2
            cv2.rectangle(frame, (tx-10, ty-40), (tx+text_size[0]+10, ty+10), (0,0,0), -1)
            cv2.putText(frame, msg_center, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)

        # 타이머 바 (하단)
        if timer_ratio > 0:
            bar_width = int(w * timer_ratio)
            cv2.rectangle(frame, (0, h - 20), (bar_width, h), (0, 255, 255), -1)

    def _process_vad_cycle(self, total_timeout=15.0):
        """
        [핵심 기능] face.py 스타일의 VAD 로직 수행
        1. 입 열림 감지 -> START_RECORDING 전송
        2. 입 닫힘 유지(2초) 감지 -> STOP_RECORDING 전송
        """
        print("👄 [VAD] 대화 감지 시작...")
        
        start_wait_time = time.time()
        is_speaking = False
        last_mouth_open_time = 0
        
        while not self.stop_event.is_set():
            # 전체 타임아웃 체크 (사용자가 아예 말을 안 걸 때)
            if not is_speaking and (time.time() - start_wait_time > total_timeout):
                print("⌛ VAD 대기 시간 초과")
                return False

            frame = None
            try: frame = self.video_frame_q.get_nowait()
            except queue.Empty: time.sleep(0.05); continue

            # 얼굴 분석
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            res = self.landmarker.detect(mp_image)
            
            mouth_score = 0.0
            if res.face_blendshapes and res.face_blendshapes[0]:
                bs = res.face_blendshapes[0]
                mouth_score = self._get_blendshape_score(bs, 'jawOpen')

            current_time = time.time()
            is_mouth_open = mouth_score > self.MOUTH_OPEN_THRESHOLD

            # --- VAD 상태 머신 ---
            if is_mouth_open:
                last_mouth_open_time = current_time
                
                # 1. 말을 시작함 (Idle -> Speaking)
                if not is_speaking:
                    print("👄 입 열림! 녹음 시작 신호 전송")
                    is_speaking = True
                    self.result_q.put({"type": "vad_status", "status": "START_RECORDING"})

            else:
                # 입을 다물고 있는 상태
                if is_speaking:
                    # 2. 말을 끝냈는지 체크 (Speaking -> Finished)
                    silence_duration = current_time - last_mouth_open_time
                    
                    # UI 피드백 (남은 시간 표시)
                    remain = max(0, self.SPEAKING_TIMEOUT_SEC - silence_duration)
                    progress = 1.0 - (remain / self.SPEAKING_TIMEOUT_SEC)
                    msg = f"Listening... {int(progress*100)}%"
                    self._draw_ui(frame, "Say Yes or No!", msg, progress)
                    
                    if silence_duration > self.SPEAKING_TIMEOUT_SEC:
                        print("👄 2초간 침묵! 녹음 종료 신호 전송")
                        self.result_q.put({"type": "vad_status", "status": "STOP_RECORDING"})
                        return True # 사이클 정상 완료
            
            # UI (대기 중)
            if not is_speaking:
                self._draw_ui(frame, "Waiting for speech...", "Open mouth to answer!")

            # (디버깅용) 얼굴 랜드마크 그리기 등은 생략하거나 필요시 추가
            time.sleep(0.03)
            
        return False

    def _run_quiz_round(self, correct_answer):
        """퀴즈 한 라운드 진행 (O/X 선택)"""
        start_time = time.time()
        timeout = 10.0
        lock_in_time = 0
        current_choice = None 
        lock_in_duration = 1.5 

        print(f"🎮 퀴즈 시작! 정답: {correct_answer}")
        
        choice_result = None
        
        while time.time() - start_time < timeout and not self.stop_event.is_set():
            frame = None
            try: frame = self.video_frame_q.get_nowait()
            except queue.Empty: time.sleep(0.05); continue

            h, w = frame.shape[:2]
            cx = w // 2
            
            # O/X 라인 그리기
            cv2.line(frame, (cx, 0), (cx, h), (255, 255, 255), 2)
            cv2.putText(frame, "X", (50, 150), cv2.FONT_HERSHEY_TRIPLEX, 4, (0, 0, 255), 5)
            cv2.putText(frame, "O", (w - 150, 150), cv2.FONT_HERSHEY_TRIPLEX, 4, (0, 255, 0), 5)

            # 얼굴 분석
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            res = self.landmarker.detect(mp_image)
            
            detected = False
            user_x = -1
            if res.face_landmarks:
                user_x = int(res.face_landmarks[0][1].x * w)
                detected = True
                cv2.circle(frame, (user_x, h//2), 15, (255, 0, 255), -1)

            # 선택 로직
            status_msg = "Move to O or X!"
            timer_ratio = 1.0 - ((time.time() - start_time) / timeout)
            
            if detected:
                temp_choice = "X" if user_x < cx else "O"
                if temp_choice == current_choice:
                    elapsed = time.time() - lock_in_time
                    if elapsed >= lock_in_duration:
                        choice_result = temp_choice
                        self._draw_ui(frame, "", f"Selected: {temp_choice}!", 1.0)
                        break # 선택 완료
                    
                    progress = elapsed / lock_in_duration
                    status_msg = f"Holding {temp_choice}... {int(progress*100)}%"
                else:
                    current_choice = temp_choice
                    lock_in_time = time.time()
            else:
                current_choice = None; lock_in_time = time.time()
                status_msg = "Face not found!"

            self._draw_ui(frame, f"Answer: {correct_answer} (Secret)", status_msg, timer_ratio)
            time.sleep(0.03)

        return choice_result

def ox_quiz_game_worker(command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
    """
    [워커 메인]
    1. START_OX_QUIZ -> O/X 게임 진행
    2. WAIT_FOR_RESPONSE -> VAD 사이클(입 열기->닫기) 진행
    """
    game = OxQuizGame(command_q, result_q, video_frame_q)
    print("▶ OX 워커 실행됨")
    
    while not game.stop_event.is_set():
        try:
            cmd = command_q.get(timeout=0.1)
            if isinstance(cmd, dict):
                c_type = cmd.get("command")
                
                if c_type == "START_OX_QUIZ":
                    # 1. 퀴즈 진행
                    ans = cmd.get("answer", "O")
                    
                    # 난이도 설정
                    if game.current_score >= 10: game.difficulty = "CRAZY"
                    elif game.current_score >= 5: game.difficulty = "HARD"
                    else: game.difficulty = "NORMAL"
                    
                    user_choice = game._run_quiz_round(ans)
                    
                    is_correct = (user_choice == ans)
                    if is_correct: game.current_score += 1
                    
                    # 다음 난이도 미리 계산
                    next_diff = "NORMAL"
                    if game.current_score >= 10: next_diff = "CRAZY"
                    elif game.current_score >= 5: next_diff = "HARD"
                    
                    game.result_q.put({
                        "type": "quiz_result",
                        "is_correct": is_correct,
                        "current_score": game.current_score,
                        "difficulty": next_diff,
                        "user_choice": user_choice
                    })

                elif c_type == "WAIT_FOR_RESPONSE":
                    # 2. 대답 대기 (VAD 사이클)
                    # 로봇이 "한 번 더 할래?" 라고 물은 직후 호출됨
                    print("🗣️ 대답 대기 모드 진입")
                    success = game._process_vad_cycle(total_timeout=15.0)
                    
                    if not success:
                        # 타임아웃 등으로 실패 시
                        game.result_q.put({"type": "vad_status", "status": "TIMEOUT"})

                elif c_type == "STOP":
                    break
                    
        except queue.Empty:
            continue
        except Exception as e:
            print(f"❌ 워커 오류: {e}")
            time.sleep(1)

    if game.landmarker: game.landmarker.close()