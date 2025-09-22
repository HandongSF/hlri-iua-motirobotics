# function/ox_game.py (수정 완료된 최종 코드)

import cv2
import mediapipe as mp
import time
import queue
import threading
import os
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

class OxQuizGame:
    def __init__(self, command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
        self.command_q = command_q
        self.result_q = result_q
        self.video_frame_q = video_frame_q
        self.stop_event = threading.Event()

        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        model_path = os.path.join(project_root, 'models/face_landmarker.task')

        try:
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.IMAGE,
                num_faces=20,
                min_face_detection_confidence=0.5
            )
            self.landmarker = vision.FaceLandmarker.create_from_options(options)
            print("✅ OX퀴즈용 얼굴 인식(FaceLandmarker) 모델 로딩 완료.")
        except Exception as e:
            print(f"❌ 얼굴 인식 모델 로딩 실패: {e}")
            self.landmarker = None
            self.stop_event.set()

    def _run_one_round(self, correct_answer: str):
        """
        한 라운드의 퀴즈를 진행하고 결과를 result_q에 넣는 단일 책임 함수.
        """
        if self.landmarker is None:
            self.result_q.put({"status": "error", "message": "모델이 로드되지 않았습니다."})
            return

        print(f"💡 OX퀴즈 라운드 시작! 정답: '{correct_answer}'. 5초 동안 인식합니다.")
        
        COUNTING_DURATION = 5
        end_time = time.time() + COUNTING_DURATION
        final_left_count, final_right_count = 0, 0
        last_frame_time = 0

        while time.time() < end_time and not self.stop_event.is_set():
            frame_data = None
            try:
                while not self.video_frame_q.empty():
                    frame_data = self.video_frame_q.get_nowait()

                if frame_data is None or frame_data.get('timestamp') == last_frame_time:
                    time.sleep(0.05)
                    continue
                
                last_frame_time = frame_data['timestamp']
                frame = frame_data['frame']

                h, w = frame.shape[:2]
                center_x = w // 2
                
                # ▼▼▼ 수정된 부분: 현재 프레임의 카운트 변수 ▼▼▼
                current_left, current_right = 0, 0

                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                detection_result = self.landmarker.detect(mp_image)

                if detection_result.face_landmarks:
                    for face_landmarks in detection_result.face_landmarks:
                        nose_landmark = face_landmarks[1]
                        face_x_position = int(nose_landmark.x * w)
                        if face_x_position < center_x:
                            current_left += 1
                        else:
                            current_right += 1
                
                # ▼▼▼ 수정된 부분: 매 프레임마다 최종 카운트를 업데이트 ▼▼▼
                final_left_count = current_left
                final_right_count = current_right
            
            except queue.Empty:
                time.sleep(0.05)
                continue

        # 5초 후 최종 결과 판정
        # ▼▼▼ 수정된 부분: 루프가 끝난 후, 최종 집계된 값으로 total_count를 계산합니다. ▼▼▼
        total_count = final_left_count + final_right_count
        winner_count = 0
        if correct_answer == "O":
            winner_count = final_right_count
        elif correct_answer == "X":
            winner_count = final_left_count

        if winner_count > 0:
            result_data = {"status": "winners_exist", "winner_count": winner_count, "total_count": total_count}
        else:
            result_data = {"status": "no_winners", "winner_count": 0, "total_count": total_count}
        
        self.result_q.put(result_data)
        print(f"🏁 OX퀴즈 라운드 종료. 결과 전송: {result_data}")

    def start_worker(self):
        """워커 스레드를 시작하고 명령을 기다립니다."""
        print("▶ OX퀴즈(얼굴인식) 워커 대기 중...")
        while not self.stop_event.is_set():
            try:
                command_data = self.command_q.get(timeout=1.0) 

                if isinstance(command_data, dict):
                    command = command_data.get("command")
                    answer = command_data.get("answer")

                    # ▼▼▼ 수정된 부분: Game Rounds 로직을 제거하고 단순화 ▼▼▼
                    if command in ["START_OX_QUIZ", "NEXT_ROUND"] and answer in ["O", "X"]:
                        self._run_one_round(answer) # 👈 바로 한 라운드를 실행하고 결과를 큐에 넣음
                    
                    elif command == "STOP":
                        break
            except queue.Empty:
                continue
        
        if self.landmarker:
            self.landmarker.close()
        print("■ OX퀴즈(얼굴인식) 워커 정상 종료")
        
def ox_quiz_game_worker(command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
    """OX 퀴즈 게임 워커를 실행하는 함수"""
    game = OxQuizGame(command_q, result_q, video_frame_q)
    game.start_worker()