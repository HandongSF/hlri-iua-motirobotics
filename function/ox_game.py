# ox_game.py

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
    얼굴 위치 기반 OX 퀴즈 게임 워커 클래스.
    - 정답자가 있으면 다음 라운드를 위해 대기.
    - 정답자가 없으면 게임 종료.
    """
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
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self.landmarker = vision.FaceLandmarker.create_from_options(options)
            print("✅ OX퀴즈용 얼굴 인식(FaceLandmarker) 모델 로딩 완료.")
        except Exception as e:
            print(f"❌ 얼굴 인식 모델 로딩 실패: {e}")
            self.landmarker = None
            self.stop_event.set()

    def _run_one_round(self, correct_answer: str) -> dict:
        """
        한 라운드의 퀴즈를 진행하고 결과를 반환하는 내부 로직.
        10초간 얼굴을 인식하여 정답자 수를 계산.
        """
        if self.landmarker is None:
            return {"status": "no_winners"}

        print(f"💡 OX퀴즈 라운드 시작! 정답: '{correct_answer}'. 10초 동안 인식합니다.")
        
        COUNTING_DURATION = 10
        end_time = time.time() + COUNTING_DURATION
        final_left_count, final_right_count = 0, 0

        while time.time() < end_time and not self.stop_event.is_set():
            frame = None
            while not self.video_frame_q.empty():
                try:
                    frame = self.video_frame_q.get_nowait()
                except queue.Empty:
                    break
            
            if frame is None:
                time.sleep(0.05)
                continue

            h, w = frame.shape[:2]
            center_x = w // 2
            left_count, right_count = 0, 0

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            detection_result = self.landmarker.detect(mp_image)

            if detection_result.face_landmarks:
                for face_landmarks in detection_result.face_landmarks:
                    nose_landmark = face_landmarks[1]
                    face_x_position = int(nose_landmark.x * w)
                    
                    if face_x_position < center_x:
                        left_count += 1
                    else:
                        right_count += 1
            
            final_left_count = left_count
            final_right_count = right_count
            time.sleep(0.05)

        # 10초 후 최종 결과 판정
        winner_count = 0
        if correct_answer == "O":
            winner_count = final_right_count
        elif correct_answer == "X":
            winner_count = final_left_count

        if winner_count > 0:
            return {"status": "winners_exist", "winner_count": winner_count}
        else:
            return {"status": "no_winners"}

    def _run_game_rounds(self, first_answer: str):
        """
        여러 라운드로 구성된 게임 전체를 관리하는 메인 루프.
        """
        current_answer = first_answer
        round_num = 1

        while not self.stop_event.is_set():
            # 1. 한 라운드 실행
            round_result = self._run_one_round(current_answer)

            # 2. 결과에 따라 분기 처리
            if round_result["status"] == "winners_exist":
                winner_count = round_result["winner_count"]
                result_text = f"정답입니다! {winner_count}명이 살아남았습니다. 다음 문제 갑니다!"
                print(f"✅ 라운드 {round_num} 결과: {result_text}")
                self.result_q.put(result_text)
                round_num += 1

                # 3. 다음 문제와 정답을 기다림 (메인 프로세스에서 보내줄 때까지)
                try:
                    print("▶ 다음 문제의 정답을 기다립니다...")
                    next_command = self.command_q.get(timeout=60.0) # 60초 타임아웃
                    
                    if isinstance(next_command, dict) and next_command.get("command") == "NEXT_ROUND":
                        current_answer = next_command.get("answer")
                        if current_answer not in ["O", "X"]:
                            self.result_q.put("오류: 다음 문제의 정답이 올바르지 않아 게임을 종료합니다.")
                            break
                    else:
                        # "NEXT_ROUND"가 아니면 게임 종료
                        break
                except queue.Empty:
                    self.result_q.put("시간 초과! 다음 문제가 없어 게임을 종료합니다.")
                    break
            else: # 정답자가 없는 경우
                result_text = "아쉽네요. 맞힌 분이 없어요. 다음에 다시 도전해주세요!"
                print(f"✅ 라운드 {round_num} 결과: {result_text}")
                self.result_q.put(result_text)
                break # 게임 루프 탈출
        
        print("🏁 OX 퀴즈 게임 세션 종료.")


    def start_worker(self):
        """워커 스레드를 시작하고 명령을 기다립니다."""
        print("▶ OX퀴즈(얼굴인식) 워커 대기 중...")
        while not self.stop_event.is_set():
            try:
                # 👈 get_nowait()으로 변경해서 기다리지 않고 바로 확인합니다.
                command_data = self.command_q.get_nowait() 

                if isinstance(command_data, dict) and command_data.get("command") == "START_OX_QUIZ":
                    initial_answer = command_data.get("answer")
                    if initial_answer in ["O", "X"]:
                        self._run_game_rounds(initial_answer)
                    else:
                        self.result_q.put("오류: 퀴즈의 정답('O' 또는 'X')이 지정되지 않았습니다.")
                elif command_data == "STOP":
                    break
            except queue.Empty:
                # 👈 큐가 비어있으면 오류 대신 이 부분이 실행됩니다.
                # 0.1초만 쉬고 바로 while 루프의 처음으로 돌아갑니다.
                time.sleep(0.1) 
                continue
        
        if self.landmarker:
            self.landmarker.close()
        print("■ OX퀴즈(얼굴인식) 워커 정상 종료")
        
    def stop(self):
        self.stop_event.set()

def ox_quiz_game_worker(command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
    """OX 퀴즈 게임 워커를 실행하는 함수"""
    game = OxQuizGame(command_q, result_q, video_frame_q)
    game.start_worker()