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

    def _run_game_logic(self, robot_choice_key):
        """
        [수정됨] 투표 시스템 도입 (Voting System)
        - 즉시 종료하지 않고 일정 시간 동안 인식된 제스처를 모음
        - 가장 많이 나온 제스처를 최종 선택 (주먹->보 전환 과정의 오류 방지)
        """
        print(f"💡 [Vision] 판독 시작! 로봇의 패: {robot_choice_key}")

        # 큐 비우기
        while not self.video_frame_q.empty():
            try: self.video_frame_q.get_nowait()
            except queue.Empty: break

        # 투표함 (인식된 동작들을 저장할 리스트)
        gesture_votes = []
        
        start_time = time.time()
        # [핵심] 1.5초 동안 꾸준히 지켜봄 (즉시 break 하지 않음)
        COLLECTION_TIME = 1.5 
        
        while time.time() - start_time < COLLECTION_TIME and not self.stop_event.is_set():
            try:
                frame = self.video_frame_q.get(timeout=0.05)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                result = self.recognizer.recognize(mp_image)
                
                if result.gestures:
                    gesture = result.gestures[0][0]
                    # 점수가 기준 이상인 것만 투표함에 넣음
                    if gesture.score > 0.5 and gesture.category_name in ["Victory", "Closed_Fist", "Open_Palm"]:
                        gesture_votes.append(gesture.category_name)
                        
            except queue.Empty:
                continue

        # --- [투표 결과 집계] ---
        if not gesture_votes:
            # 하나도 인식 못 함
            self.result_q.put({"status": "error", "msg": "손이 안 보였어요."})
            return

        # 가장 많이 나온 제스처 찾기 (최빈값)
        from collections import Counter
        vote_counts = Counter(gesture_votes)
        best_gesture = vote_counts.most_common(1)[0][0] # 가장 많이 등장한 제스처
        
        print(f"🗳️ 투표 결과: {dict(vote_counts)} -> 최종 선정: {best_gesture}")

        # 매핑
        mapping = {"Victory": "Scissors", "Closed_Fist": "Rock", "Open_Palm": "Paper"}
        user_choice = mapping.get(best_gesture, "Rock")
        
        # 승패 판정
        final_result = "DRAW"
        if user_choice == robot_choice_key:
            final_result = "DRAW"
        elif (user_choice == "Rock" and robot_choice_key == "Scissors") or \
             (user_choice == "Paper" and robot_choice_key == "Rock") or \
             (user_choice == "Scissors" and robot_choice_key == "Paper"):
            final_result = "USER_WIN"
        else:
            final_result = "ROBOT_WIN"

        # 결과 전송
        self.result_q.put({
            "status": "success",
            "result": final_result,
            "user_choice": user_choice,
            "robot_choice": robot_choice_key
        })

    def start_worker(self):
        print("▶ 가위바위보 워커 대기 중...")
        while not self.stop_event.is_set():
            try:
                msg = self.command_q.get(timeout=1.0)
                # 딕셔너리 형태의 명령을 받음
                if isinstance(msg, dict) and msg.get("command") == "START_GAME":
                    robot_choice = msg.get("robot_choice", "Rock") # 기본값 Rock
                    self._run_game_logic(robot_choice)
                elif msg == "STOP":
                    break
            except queue.Empty:
                continue
        self.recognizer.close()
        
    def stop(self):
        self.stop_event.set()

def rock_paper_game_worker(command_q: queue.Queue, result_q: queue.Queue, video_frame_q: queue.Queue):
    game = RockPaperGame(command_q, result_q, video_frame_q)
    game.start_worker()
