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

# function/entertain.py
from __future__ import annotations
import time
import json
import random
import threading
import queue
import re
import google.generativeai as genai
from typing import TYPE_CHECKING

# 순환 참조를 피하기 위해 타입 힌트만 임포트
if TYPE_CHECKING:
    from gemini_api import PressToTalk

# gemini_api.py에 있는 전역 헬퍼 함수 임포트
from function.utils import _extract_text

class EntertainmentHandler:
    """
    농담, OX 퀴즈, 가위바위보 등 엔터테인먼트 기능을 전담하는 클래스
    """
    def __init__(self, ptt_instance: 'PressToTalk'):
        self.ptt = ptt_instance
        self.MODEL_NAME = ptt_instance.MODEL_NAME # PressToTalk에서 모델 이름 가져오기

    def run_joke(self):
        """농담 생성 및 실행 로직 (gemini_api.py에서 이동)"""
        try:
            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("THINKING")
            self.ptt._speak_and_subtitle("위잉 회로 풀가동! 여러분의 모터가 빠질만한 개그를 생성하는 중입니다")
            self.ptt.tts.wait()
            joke_prompt = (
                "너는 '모티'라는 로봇이야. '로봇', '컴퓨터', '전기'와 관련된, 어린아이도 이해할 수 있는 매우 창의적인 아재개그를 딱 하나만 만들어줘. "
                "이전에 만들었던 농담과는 다른 새로운 농담이어야 해. "
                "중요한 규칙: '삐빅' 같은 로봇 효과음은 절대 넣지 마. "
                "출력은 반드시 다음 JSON 형식이어야 해. 다른 설명은 절대로 추가하지 마.\n"
                '{ "question": "<퀴즈 형식의 질문>", "answer": "<짧은 답변>", "explanation": "왜냐하면, <답변에 대한 1~2문장의 유머러스한 설명>" }'
            )

            joke_data = None
            try:
                joke_response = genai.GenerativeModel(self.MODEL_NAME).generate_content(
                    joke_prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                raw_json = _extract_text(joke_response)
                joke_data = json.loads(raw_json)

            except Exception as e:
                print(f"    - ❌ 농담 생성 실패: {e}")
                fallback_joke = "앗, 재미있는 농담이 떠오르지 않네요. 다음에 다시 시도해주세요!"
                print(f"🔊 TTS SAYING: {fallback_joke}")
                self.ptt._speak_and_subtitle(fallback_joke)
                self.ptt.tts.wait()
            
            if joke_data:
                question = joke_data.get("question", "질문이 없네요.")
                answer = joke_data.get("answer", "답변이 없네요.")
                explanation = joke_data.get("explanation", "왜냐하면, 설명이 없네요.")
                
                print(f'🔊 TTS SAYING (Q): "{question}"')
                self.ptt._speak_and_subtitle(question)
                self.ptt.tts.wait()

                print("    - (5초 대기...)")
                time.sleep(5)
                
                print(f'🔊 TTS SAYING (A): "{answer}"')
                self.ptt._speak_and_subtitle(answer)
                self.ptt.tts.wait()
                
                if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY")
                
                print(f'🔊 TTS SAYING (E): "{explanation}"')
                self.ptt._speak_and_subtitle(explanation)
                self.ptt.tts.wait()
                
        finally:
            if self.ptt.emotion_queue:
                self.ptt.emotion_queue.put("NEUTRAL")
            pass

    def run_ox_quiz(self):
        """
        [수정됨] 1:1 인터랙티브 OX 퀴즈 게임 (VAD 적용)
        """
        print("💡 의도: OX QUIZ GAME (1:1 VAD Ver.)")

        if not self.ptt.shared_state or not self.ptt.ox_command_q:
            self.ptt._speak_and_subtitle("OX 퀴즈 시스템을 시작할 수 없어요.")
            return
        
        # --- [1] 퀴즈 데이터셋 ---
        predefined_quizzes = [
            {"question": "제 이름은 '모터'입니다", "answer": "X", "explanation": "제 이름은 모티, 모티예요! 꼭 기억해주세요."},
            {"question": "모티는 공감 서비스 로봇입니다", "answer": "O", "explanation": "저는 여러분의 마음을 이해하고 공감하기 위해 만들어졌어요."},
            {"question": "모티는 나름 유명한 유튜버이다", "answer": "O", "explanation": "숨겨진 차원의 학부생들 구독과 좋아요! 알림 설정까지 꾸욱 눌러주세요!"},
            {"question": "딸기는 장미과에 속한다?", "answer": "O", "explanation": "놀랍게도 딸기는 장미과 식물이랍니다!"},
            {"question": "바나나는 나무에서 자란다?", "answer": "X", "explanation": "바나나는 사실 거대한 '풀'이에요!"},
        ]

        # 크레이지 모드 문제 (점수가 높아지면 출제)
        crazy_mode_quizzes = [
            {"question": "여러번을 강조할 때 골백번이라고 흔히 말하는데 골은 10000을 뜻한다.",
                "answer": "O",
                "explanation": "이 정도는 맞춰줘야죠!"},
            {"question": "눈을 뜨고는 재체기를 할 수 없다.",
                "answer": "O",
                "explanation": "눈을 뜨고 재치기 하는 것은 거의 불가능에 가깝습니다."},
            {"question": "개미는 높은 곳에서 떨어지면 죽는다는 말이... 틀렸다는 것을 부정하는 것은 옳지 않다.",
                "answer": "O",
                "explanation": "개미는 높은 곳에서 떨어져도 죽지 않아요"},
            { "question": "모티의 이름은 8월 12일에 지어졌다... 라는 문장에 들어간 ㅇ의 개수는 8개이다.",
                "answer": "X",
                "explanation": "해당 문장에서 ㅇ은 총 7개입니다."}
        ]

        # --- [2] 게임 시작 멘트 ---
        try:
            self.ptt.shared_state['mode'] = 'ox_quiz'
            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY")
            
            self.ptt._speak_and_subtitle("지금부터 OX 퀴즈를 시작합니다! 제가 내는 문제를 듣고...")
            self.ptt.tts.wait()
            self.ptt._speak_and_subtitle("맞으면 오른쪽 O, 틀리면 왼쪽 X로 얼굴을 움직여주세요!")
            self.ptt.tts.wait()

            quiz_idx = 0
            current_score = 0
            
            while not self.ptt.stop_event.is_set():
                # --- [3] 문제 선정 (난이도 조절) ---
                quiz_data = None
                difficulty = "NORMAL"
                
                # 3점 이상이면 크레이지 모드 진입
                if current_score >= 3:
                    difficulty = "CRAZY"

                if difficulty == "CRAZY" and crazy_mode_quizzes:
                    quiz_data = crazy_mode_quizzes.pop(0)
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("ANGRY") # 장난스러운 표정
                    self.ptt._speak_and_subtitle("후후.. 당신 실력이 대단하군요. 이제 매운맛을 보여주지!")
                    self.ptt.tts.wait()
                elif quiz_idx < len(predefined_quizzes):
                    quiz_data = predefined_quizzes[quiz_idx]
                    quiz_idx += 1
                else:
                    # 준비된 문제가 떨어지면 Gemini에게 생성 요청
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("THINKING")
                    self.ptt._speak_and_subtitle("잠시만요, 새로운 문제를 만들고 있어요.")
                    try:
                        prompt = "재미있는 상식 OX 퀴즈 1개만 JSON으로 만들어줘. {question, answer(O/X), explanation}"
                        resp = genai.GenerativeModel(self.MODEL_NAME).generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                        quiz_data = json.loads(_extract_text(resp))
                    except:
                        quiz_data = {"question": "모티는 귀엽다?", "answer": "O", "explanation": "당연한 사실이죠!"}
                        
                # --- [4] 랜덤 추임새 (NEW) ---
                # 첫 문제(idx=1, pop했으므로) 이후부터 50% 확률로 발동        
                if quiz_idx > 1 or current_score > 0:
                    if random.random() < 0.5:
                        if self.ptt.emotion_queue: self.ptt.emotion_queue.put("THINKING")
                        thinking_phrases = [
                            "음... 어떤 문제를 내볼까?",
                            "히히 이거 재미있겠다.",
                            "이번에는 조금 어려울 수도 있어요.",
                            "과연 맞출 수 있을까요?",
                            "인간에겐 너무 어려웠나? 쉽게 갈까요?"
                        ]
                        phrase = random.choice(thinking_phrases)
                        self.ptt._speak_and_subtitle(phrase)
                        self.ptt.tts.wait()
                        
                # --- [5] 문제 출제 ---
                if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
                self.ptt._speak_and_subtitle(f"문제! {quiz_data['question']}")
                self.ptt.tts.wait()

                # 워커에게 명령 전송 (정답 판별 요청)
                self.ptt.ox_command_q.put({"command": "START_OX_QUIZ", "answer": quiz_data["answer"]})
                
                # 결과 대기
                try:
                    result = self.ptt.ox_result_q.get(timeout=20.0) # 20초 대기
                except queue.Empty:
                    self.ptt._speak_and_subtitle("시간이 너무 오래 걸려서 다음으로 넘어갈게요.")
                    continue

                if result.get("type") != "quiz_result": continue

                # --- [6] 결과 발표 ---
                is_correct = result["is_correct"]
                current_score = result["current_score"] # 워커와 점수 동기화

                if is_correct:
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY")
                    self.ptt._speak_and_subtitle(f"정답입니다! 현재 점수는 {current_score}점!")
                else:
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("SAD")
                    self.ptt._speak_and_subtitle(f"땡! 틀렸어요. 정답은 {quiz_data['answer']}였습니다.")
                self.ptt.tts.wait()
                
                # 해설
                if "explanation" in quiz_data:
                    self.ptt._speak_and_subtitle(quiz_data["explanation"])
                    self.ptt.tts.wait()

                # --- [7] 재도전 의사 확인 (VAD Trigger) ---
                self.ptt._speak_and_subtitle("계속 하시겠어요? 하실거면 그래! 또는 좋아! 라고 말씀해주세요.")
                self.ptt.tts.wait()

                # 워커를 '입 감지 모드'로 전환
                self.ptt.ox_command_q.put({"command": "WAIT_FOR_RESPONSE"})
                
                try:
                    # 입 벌림 신호 대기 (10초)
                    vad_msg = self.ptt.ox_result_q.get(timeout=10.0) 
                    
                    if vad_msg.get("status") == "START_RECORDING":
                        # 입 벌림 감지됨 -> 즉시 듣기 시작 (Gemini API 헬퍼 함수 사용)
                        print("👄 입 벌림 감지! 의사 확인(Yes/No) 시작")
                        
                        # 워커가 보낼 수 있는 STOP_RECORDING 신호를 미리 비우거나 무시하기 위해 
                        # 별도 처리는 안 해도 되지만, 큐가 꼬이지 않게 주의
                        
                        wants_more = self.ptt._quick_listen_for_yes_no(timeout=5.0)
                        
                        # 혹시 워커가 보낸 STOP_RECORDING 메시지가 남아있다면 제거
                        try: self.ptt.ox_result_q.get_nowait()
                        except: pass 
                        
                        if wants_more:
                            self.ptt._speak_and_subtitle("좋아요! 다음 문제 나갑니다!")
                            self.ptt.tts.wait()
                            continue # 다음 루프로
                        else:
                            self.ptt._speak_and_subtitle("네, 여기서 마칠게요. 즐거웠어요!")
                            break # 게임 종료
                    else:
                        # 타임아웃 (입을 안 벌림)
                        self.ptt._speak_and_subtitle("반응이 없으시네요. 게임을 종료할게요.")
                        break

                except queue.Empty:
                    self.ptt._speak_and_subtitle("종료할게요.")
                    break

        except Exception as e:
            print(f"❌ 퀴즈 게임 오류: {e}")
            self.ptt._speak_and_subtitle("오류가 발생했습니다.")
        
        finally:
            # 워커 상태 초기화 및 모드 복귀
            self.ptt.ox_command_q.put({"command": "STOP"})
            if self.ptt.shared_state:
                self.ptt.shared_state['mode'] = 'tracking'
            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")

    def run_rps_game(self):
        """
        [수정됨] 스코어 대결 및 무한 모드 가위바위보
        - 비김: 즉시 재대결 (질문 X)
        - 승패: 감정 표현(슬픔/기쁨) -> 스코어 안내 -> 재도전 질문
        """
        print("💡 의도: RPS GAME (Score & Loop Ver.)")
        KOREAN_CHOICES = {"Rock": "바위", "Paper": "보", "Scissors": "가위"}
        
        # 스코어 초기화
        user_score = 0
        robot_score = 0
        
        try:
            # 1. 게임 시작 멘트
            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY")
            self.ptt._speak_and_subtitle("가위바위보 대결을 시작할게요. 잠시후 당신의 손동작을 보여주세요. ")
            self.ptt.tts.wait()

            while not self.ptt.stop_event.is_set():
                # --- [라운드 시작] ---
                # 로봇 패 미리 결정
                robot_choice_key = random.choice(["Rock", "Paper", "Scissors"])
                robot_choice_kr = KOREAN_CHOICES[robot_choice_key]

                self.ptt._speak_and_subtitle("준비하시고...")
                # 준비 동작 (팔 움직임 등)
                if callable(self.ptt.play_rps_motion_cb):
                    threading.Thread(target=self.ptt.play_rps_motion_cb, daemon=True).start()
                self.ptt._speak_and_subtitle("가위! 바위!")
                self.ptt.tts.wait() # "바위" 말할 때까지 대기
                self.ptt._speak_and_subtitle("보!!")
                time.sleep(0.3)
                # --- [결정적 순간] ---
                # 화면 출력 (이미지) + 비전 인식 시작
                display_key = f"RPS_{robot_choice_key.upper()}"
                if self.ptt.emotion_queue: self.ptt.emotion_queue.put(display_key)
                
                self.ptt.rps_command_q.put({
                    "command": "START_GAME", 
                    "robot_choice": robot_choice_key
                })
                time.sleep(0.3)
                self.ptt.tts.wait()

                # --- [결과 대기] ---
                try:
                    result_data = self.ptt.rps_result_q.get(timeout=5)
                except queue.Empty:
                    # 타임아웃 시
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
                    self.ptt._speak_and_subtitle("너무 늦게 내셨어요! 다시 할게요.")
                    time.sleep(1.5) # 리셋된 화면을 보여줄 시간 확보
                    continue

                if result_data.get("status") == "error":
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
                    self.ptt._speak_and_subtitle("손이 잘 안 보였어요. 다시 해볼까요?")
                    time.sleep(1.5) # 리셋된 화면을 보여줄 시간 확보
                    continue

                # --- [결과 처리] ---
                res = result_data["result"]
                user_choice_kr = KOREAN_CHOICES.get(result_data["user_choice"], "??")
                
                print(f"📊 결과: User({user_choice_kr}) vs Robot({robot_choice_kr}) -> {res}")

                # 1. 비긴 경우 -> 즉시 재대결 (질문 없이 continue)
                if res == "DRAW":
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("SURPRISED")
                    self.ptt._speak_and_subtitle(f"저도 {robot_choice_kr}! 비겼네요! 비겼으니까 바로 다시 해요!")
                    self.ptt.tts.wait()
                    time.sleep(1) # 잠시 숨 고르기
                    
                    # 감정 초기화 후 바로 루프 시작
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
                    time.sleep(1.5)
                    continue 

                # 2. 승패가 갈린 경우
                elif res == "USER_WIN":
                    user_score += 1
                    # 사용자가 이기면 -> 로봇은 슬픔 (요청사항 반영)
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("SAD") 
                    self.ptt._speak_and_subtitle(f"당신은 {user_choice_kr}, 저는 {robot_choice_kr}... 으앙 제가 졌어요.")
                
                elif res == "ROBOT_WIN":
                    robot_score += 1
                    # 사용자가 지면 -> 로봇은 행복 (요청사항 반영)
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY") 
                    self.ptt._speak_and_subtitle(f"당신은 {user_choice_kr}, 저는 {robot_choice_kr}! 아싸 제가 이겼어요!")
                
                self.ptt.tts.wait()

                # 3. 스코어 중계 (승패가 났을 때만)
                score_msg = f"현재 스코어 {user_score} 대 {robot_score}."
                if user_score > robot_score:
                    score_msg += " 사용자님이 이기고 계시네요!"
                elif robot_score > user_score:
                    score_msg += " 제가 이기고 있어요! 메롱!"
                else:
                    score_msg += " 동점이에요! 막상막하네요."
                
                self.ptt._speak_and_subtitle(score_msg)
                self.ptt.tts.wait()

                # 4. 재도전 여부 확인 (OX 퀴즈 스타일)
                self.ptt._speak_and_subtitle("계속 하시겠어요? (응/아니)")
                self.ptt.tts.wait()

                # 음성 인식 (VAD)
                wants_more = self.ptt._quick_listen_for_yes_no(timeout=5.0)

                if wants_more:
                    self.ptt._speak_and_subtitle("좋아요! 덤비세요!")
                    self.ptt.tts.wait()
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
                    time.sleep(1)
                    continue # 루프 계속
                else:
                    self.ptt._speak_and_subtitle(f"네, 좋은 승부였어요! 최종 스코어는 {user_score} 대 {robot_score}입니다.")
                    self.ptt.tts.wait()
                    break # 게임 종료

        except Exception as e:
            print(f"❌ 가위바위보 게임 오류: {e}")
            self.ptt._speak_and_subtitle("앗, 회로가 엉켰어요. 게임을 종료할게요.")
        
        finally:
            # 게임 종료 후 정리
            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
            self.ptt.lower_busy_signal()