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
                min_face_detection_confidence=0.3, # 💡 만약 인식률이 부족하면 이 값을 0.3으로 낮춰보세요.
                min_face_presence_confidence=0.3,
                min_tracking_confidence=0.3,
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

            # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
            # ✨ [솔루션 2 적용] 이미지 해상도를 1.5배 키워서 인식률을 높입니다.
            h, w = frame.shape[:2]
            upscaled_frame = cv2.resize(frame, (int(w * 1.5), int(h * 1.5)), interpolation=cv2.INTER_LINEAR)
            
            # 인식할 때 사용할 프레임의 높이, 너비를 다시 계산합니다.
            h_up, w_up = upscaled_frame.shape[:2]
            center_x = w_up // 2
            
            left_count, right_count = 0, 0

            # 원본 frame 대신 해상도를 높인 upscaled_frame을 모델에 입력합니다.
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(upscaled_frame, cv2.COLOR_BGR2RGB))
            detection_result = self.landmarker.detect(mp_image)
            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
            
            if detection_result.face_landmarks:
                for face_landmarks in detection_result.face_landmarks:
                    nose_landmark = face_landmarks[1]
                    # 좌표 계산 시 커진 이미지의 너비(w_up)를 기준으로 사용해야 합니다.
                    face_x_position = int(nose_landmark.x * w_up)
                    
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

    def _run_game_rounds(self, first_answer: str, is_predefined: bool):
        """
        여러 라운드로 구성된 게임 전체를 관리하는 메인 루프.
        """
        current_answer = first_answer
        current_is_predefined = is_predefined
        round_num = 1

        while not self.stop_event.is_set():
            # 1. 한 라운드 실행
            round_result = self._run_one_round(current_answer)
            message = ""
            winner_count = 0

            # 2. 결과 분석 및 메시지 생성
            if round_result["status"] == "winners_exist" or current_is_predefined:
                winner_count = round_result.get("winner_count", 0)
                
                if current_is_predefined:
                    message = "계속 진행해볼게요!"
                elif winner_count == 1:
                    message = "최후의 승자가 탄생했습니다! 모두 축하의 박수를 보내주세요!"
                else:
                    message = f"{winner_count}명이 살아남았습니다. 다음 문제 갑니다!"
            else: # 정답자가 없는 경우
                message = "아쉽게도 모두 탈락했네요. 다음에 다시 도전해봐요!"
                winner_count = 0

            print(f"✅ 라운드 {round_num} 결과: {message}")
            
            # 3. 메인 로직으로 결과 전송
            result_to_send = {
                "message": message,
                "winner_count": winner_count,
                "is_predefined": current_is_predefined
            }
            self.result_q.put(result_to_send)
            
            # ✨ 4. 게임 종료 여부 판단 (가장 중요한 변경점)
            if not current_is_predefined and winner_count <= 1:
                # 실제 게임에서 1명 이하가 남으면 워커의 임무는 끝. 즉시 루프 탈출.
                break 

            # ✨ 5. 게임이 계속될 경우에만 다음 명령을 기다림
            try:
                print("▶ 다음 문제의 정답과 상태를 기다립니다...")
                next_command = self.command_q.get(timeout=60.0)
                
                if isinstance(next_command, dict) and next_command.get("command") == "NEXT_ROUND":
                    current_answer = next_command.get("answer")
                    current_is_predefined = next_command.get("is_predefined", False)

                    if current_answer not in ["O", "X"]:
                        # ... 오류 처리 ...
                        break
                    # ✨ 성공적으로 다음 명령을 받으면, 루프의 처음으로 돌아감 (continue 불필요)
                else:
                    # NEXT_ROUND가 아닌 다른 명령이 오면 게임 세션 종료
                    break
                
            except queue.Empty:
                print("⌛ 다음 명령 타임아웃. 워커를 종료합니다.")
                break
            
            round_num += 1
        
        print("🏁 OX 퀴즈 게임 워커 세션 종료.")


    def start_worker(self):
        """워커 스레드를 시작하고 명령을 기다립니다."""
        print("▶ OX퀴즈(얼굴인식) 워커 대기 중...")
        while not self.stop_event.is_set():
            try:
                # 👈 get_nowait()으로 변경해서 기다리지 않고 바로 확인합니다.
                command_data = self.command_q.get_nowait() 

                if isinstance(command_data, dict) and command_data.get("command") == "START_OX_QUIZ":
                    initial_answer = command_data.get("answer")
                    is_predefined = command_data.get("is_predefined", False)

                    if initial_answer in ["O", "X"]:
                        self._run_game_rounds(initial_answer, is_predefined)
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