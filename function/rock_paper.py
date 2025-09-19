# ============================================================
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================

# rock_paper.py

import cv2
import mediapipe as mp
import numpy as np
import random
import time
import queue
import threading
import os # os 모듈 추가
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 1. 수정된 부분 (A) ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# 클래스로 전체 로직을 캡슐화하여 모델 로딩을 한 번만 수행하도록 변경합니다.
class RockPaperGame:
    def __init__(self, command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
        self.command_q = command_q
        self.result_q = result_q
        self.video_frame_q = video_frame_q
        self.stop_event = threading.Event()

        # 모델 파일 경로 설정 (상대 경로 문제 해결)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(base_dir, 'gesture_recognizer.task')

        # 제스처 인식기(GestureRecognizer) 생성
        options = vision.GestureRecognizerOptions(
            base_options=python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.IMAGE
        )
        self.recognizer = vision.GestureRecognizer.create_from_options(options)
        print("✅ 가위바위보 제스처 모델 미리 로딩 완료.")

        # 모델 예열(Warm-up)을 위해 가짜 이미지로 한 번 실행합니다.
        try:
            print("▶ 가위바위보 모델 예열 중...")
            dummy_image = np.zeros((100, 100, 3), dtype=np.uint8)
            dummy_mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=dummy_image)
            self.recognizer.recognize(dummy_mp_image)
            print("✅ 가위바위보 모델 예열 완료.")
        except Exception as e:
            print(f"⚠️ 모델 예열 중 오류 발생: {e}")

        # 최소 인식 점수 및 한국어 매핑
        self.MIN_CONFIDENCE_SCORE = 0.7
        self.KOREAN_CHOICES = {"Rock": "바위", "Paper": "보", "Scissors": "가위"}

    def _run_game_logic(self):
        """실제 게임 한 판을 실행하는 로직"""
        print("💡 게임 시작 신호 받음. 제스처를 인식합니다.")

        #--- 게임 시작 전 큐를 비웁니다. ---
        while not self.video_frame_q.empty():
            try:
                self.video_frame_q.get_nowait()
            except queue.Empty:
                break


        best_gesture = "None"
        max_confidence_score = 0.0
        recognition_started = False
        start_time = 0
        end_time = time.time() + 20 # 전체 제한 시간 20초

        while time.time() < end_time and not self.stop_event.is_set():
            try:
                frame = self.video_frame_q.get(timeout=0.1)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                recognition_result = self.recognizer.recognize(mp_image)
                
                if recognition_result.gestures:
                    top_gesture = recognition_result.gestures[0][0]
                    gesture_name = top_gesture.category_name
                    confidence_score = top_gesture.score
                    
                    if self.MIN_CONFIDENCE_SCORE <= confidence_score and gesture_name in ["Victory", "Closed_Fist", "Open_Palm"]:
                        if not recognition_started:
                            recognition_started = True
                            start_time = time.time()
                        
                        if confidence_score > max_confidence_score:
                            best_gesture = gesture_name
                            max_confidence_score = confidence_score
                            print(f"[{time.strftime('%H:%M:%S')}] Gesture: {gesture_name}, Score: {confidence_score:.2f}")
                        else:
                            print(f"[{time.strftime('%H:%M:%S')}] Gesture: None")
                                
                if recognition_started and time.time() - start_time >= 3:
                    break
            except queue.Empty:
                continue

        if best_gesture == "None":
            self.result_q.put("제스처를 인식하지 못했어요.")
            return

        user_choice_map = {"Victory": "Scissors", "Closed_Fist": "Rock", "Open_Palm": "Paper"}
        user_choice = user_choice_map.get(best_gesture, "")
        computer_choice = random.choice(["Rock", "Paper", "Scissors"])
        
        user_choice_kr = self.KOREAN_CHOICES.get(user_choice, "")
        computer_choice_kr = self.KOREAN_CHOICES.get(computer_choice, "")

        if user_choice == computer_choice:
            result_text = f"저도 {user_choice_kr}를 냈어요. 비겼네요!"
        elif (user_choice == "Rock" and computer_choice == "Scissors") or \
             (user_choice == "Paper" and computer_choice == "Rock") or \
             (user_choice == "Scissors" and computer_choice == "Paper"):
            print("사용자: " +user_choice)
            result_text = f"제가 {computer_choice_kr}를 냈네요. 당신이 이겼어요!"
        else:
            print("사용자: " +user_choice)
            result_text = f"제가 {computer_choice_kr}를 냈어요. 제가 이겼네요!"
        
        self.result_q.put(result_text)

    def start_worker(self):
        """워커 스레드를 시작하고 명령을 기다립니다."""
        print("▶ 가위바위보 워커 대기 중...")
        while not self.stop_event.is_set():
            try:
                command = self.command_q.get(timeout=1.0)
                if command == "START_GAME":
                    self._run_game_logic()
                elif command == "STOP":
                    break
            except queue.Empty:
                continue
        
        self.recognizer.close()
        print("■ 가위바위보 워커 정상 종료")
        
    def stop(self):
        self.stop_event.set()

# 워커 함수를 클래스를 사용하도록 수정
def rock_paper_game_worker(command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
    game = RockPaperGame(command_q, result_q, video_frame_q)
    game.start_worker()
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲