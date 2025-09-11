# rock_paper.py

import cv2
import mediapipe as mp
import numpy as np
import random
import time
import queue
import threading
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# 1. 모델 파일 경로 설정
model_path = 'function/gesture_recognizer.task'

# 2. 제스처 인식기(GestureRecognizer) 생성
options = vision.GestureRecognizerOptions(
    base_options=python.BaseOptions(model_asset_path=model_path),
    running_mode=vision.RunningMode.IMAGE
)
recognizer = vision.GestureRecognizer.create_from_options(options)

# 3. 최소 인식 점수 설정
MIN_CONFIDENCE_SCORE = 0.7  # 70% 이상의 확신이 있을 때만 인식

# 4. 가위바위보 이름을 한국어로 매핑하는 딕셔너리 추가
KOREAN_CHOICES = {
    "Rock": "바위",
    "Paper": "보",
    "Scissors": "가위"
}

def rock_paper_game_worker(command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
    """가위바위보 게임을 실행하는 워커 함수"""
    print("▶ 가위바위보 워커 대기 중...")
    
    while True:
        try:
            command = command_q.get(timeout=1.0)
            if command == "STOP":
                print("▶ 워커 종료 명령 받음")
                break
            
            if command == "START_GAME":
                print("💡 게임 시작 신호 받음. 제스처를 인식합니다.")
                
                # 수정된 부분: 인식 타이밍을 위한 변수 초기화
                best_gesture = "None"
                max_confidence_score = 0.0
                recognition_started = False
                start_time = 0
                
                # 수정된 부분: 20초의 전체 제한 시간을 둡니다.
                end_time = time.time() + 30
                
                while time.time() < end_time:
                    try:
                        frame = video_frame_q.get(timeout=1.0)
                        
                        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        recognition_result = recognizer.recognize(mp_image)
                        
                        if recognition_result.gestures:
                            top_gesture = recognition_result.gestures[0][0]
                            gesture_name = top_gesture.category_name
                            confidence_score = top_gesture.score
                            
                            print(f"인식 제스처: {gesture_name}, 점수: {confidence_score:.2f}")

                            if confidence_score >= MIN_CONFIDENCE_SCORE and gesture_name in ["Victory", "Closed_Fist", "Open_Palm"]:
                                # 유효한 제스처가 처음 인식되면 3초 카운트 시작
                                if not recognition_started:
                                    recognition_started = True
                                    start_time = time.time()
                                
                                # 3초간 가장 높은 점수를 기록
                                if confidence_score > max_confidence_score:
                                    best_gesture = gesture_name
                                    max_confidence_score = confidence_score
                        
                        # 인식 시작 후 3초가 지나면 루프 종료
                        if recognition_started and time.time() - start_time >= 3:
                            break
                        
                    except queue.Empty:
                        continue
                
                # 게임 로직
                if best_gesture == "None":
                    result_q.put("제스처를 인식하지 못했어요. 다음에 다시 해볼까요?")
                else:
                    user_choice = ""
                    if best_gesture == "Victory": user_choice = "Scissors"
                    elif best_gesture == "Closed_Fist": user_choice = "Rock"
                    elif best_gesture == "Open_Palm": user_choice = "Paper"
                    
                    choices = ["Rock", "Paper", "Scissors"]
                    computer_choice = random.choice(choices)
                    
                    user_choice_kr = KOREAN_CHOICES.get(user_choice, user_choice)
                    computer_choice_kr = KOREAN_CHOICES.get(computer_choice, computer_choice)
                    
                    game_result_text = ""
                    if user_choice == computer_choice:
                        game_result_text = f"저도 {user_choice_kr}를 냈어요. 비겼네요!"
                    elif (user_choice == "Rock" and computer_choice == "Scissors") or \
                         (user_choice == "Paper" and computer_choice == "Rock") or \
                         (user_choice == "Scissors" and computer_choice == "Paper"):
                        game_result_text = f"제가 {computer_choice_kr}를 냈네요. 당신이 이겼어요!"
                    else:
                        game_result_text = f"제가 {computer_choice_kr}를 냈어요. 제가 이겼네요!"

                    result_q.put(game_result_text)

        except queue.Empty:
            continue
        except Exception as e:
            print(f"❌ 워커: 알 수 없는 오류 발생: {e}")
            break
            
    recognizer.close()
    print("■ 워커 정상 종료")


if __name__ == "__main__":
    pass