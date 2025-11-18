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
        """OX 퀴즈 게임 실행 로직 (gemini_api.py에서 이동)"""
        print("💡 의도: OX QUIZ GAME (라운드 방식)")

        if not self.ptt.shared_state or not self.ptt.ox_command_q:
            self.ptt._speak_and_subtitle("시스템 오류로 퀴즈를 진행할 수 없어요.")
            print("❌ shared_state 또는 ox_command_q가 없어 모드 전환 불가")
            return
        
        predefined_quizzes = [
            {"question": "제 이름은 '모터'입니다", "answer": "X", "explanation": "제 이름은 모티, 모티예요! 꼭 기억해주세요."},
            {"question": "모티는 공감 서비스 로봇입니다", "answer": "O", "explanation": "저는 여러분의 마음을 이해하고 공감하기 위해 만들어졌어요."},
            {"question": "모티는 나름 유명한 유튜버이다", "answer": "O", "explanation": "구독과 좋아요! 알림 설정까지 꾸욱 눌러주세요!"},
        ]

        crazy_mode_quizzes = {
            3: { # 4번째 본 게임 라운드
                "question": "여러번을 강조할 때 골백번이라고 흔히 말하는데 골은 10000을 뜻한다.",
                "answer": "O",
                "explanation": "이 정도는 맞춰줘야죠!"
            },
            4: { # 5번째 본 게임 라운드
                "question": "눈을 뜨고는 재체기를 할 수 없다.",
                "answer": "O",
                "explanation": "눈을 뜨고 재치기 하는 것은 거의 불가능에 가깝습니다."
            },
            5: { # 6번째 본 게임 라운드
                "question": "개미는 높은 곳에서 떨어지면 죽는다는 말이... 틀렸다는 것을 부정하는 것은 옳지 않다.",
                "answer": "O",
                "explanation": "개미는 높은 곳에서 떨어져도 죽지 않아요"
            },
            6: { # 7번째 본 게임 라운드
                "question": "모티의 이름은 8월 12일에 지어졌다... 라는 문장에 들어간 ㅇ의 개수는 8개이다.",
                "answer": "X",
                "explanation": "해당 문장에서 ㅇ은 총 7개입니다."
            }
        }

        quiz_result_container = []
        # gemini_api.py 원본 코드에서 self.generated_quizzes가 __init__에 선언되지 않았으므로
        # 여기서는 지역 변수로 선언합니다.
        generated_quizzes = []

        quiz_round_counter = 0
        main_game_round_counter = 0
        is_first_round = True
        is_game_over = False
        is_main_game_started = False
        is_crazy_mode_active = False 

        try:
            self.ptt.shared_state['mode'] = 'ox_quiz'
            self.ptt._speak_and_subtitle("그럼 지금부터 여러분과 OX 퀴즈 게임을 시작하겠습니다!")
            self.ptt.tts.wait()
            
            self.ptt._speak_and_subtitle("제가 내는 질문을 듣고 맞다고 생각하면... 동그라미가 그려진 오른쪽으로 이동해주세요!... 틀리다고 생각하면 엑스가 그려진 왼쪽으로... 이동해주세요! ")
            self.ptt.tts.wait()
            
            self.ptt._speak_and_subtitle("먼저, 몸풀기로 연습문제를 몇 개 풀어볼게요. 첫 문제 나갑니다!")
            self.ptt.tts.wait()

            quiz_fetch_thread = threading.Thread(
                target=self.ptt._fetch_quizzes_in_background, # self.ptt의 헬퍼 함수 호출
                args=(quiz_result_container,)
            )
            quiz_fetch_thread.start()

            while not is_game_over and not self.ptt.stop_event.is_set():
                if not is_first_round and not is_predefined:
                    print("  - 🤔 다음 라운드 준비를 위해 THINKING 표정으로 변경")
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("THINKING")
                    time.sleep(1)

                quiz_data = None
                is_predefined = False

                if quiz_round_counter < len(predefined_quizzes):
                    quiz_data = predefined_quizzes[quiz_round_counter]
                    is_predefined = True 
                    print(f" - 사전 정의된 퀴즈 #{quiz_round_counter + 1} 사용: {quiz_data}")
                    quiz_round_counter += 1
                else:
                    if not is_main_game_started:
                        self.ptt._speak_and_subtitle("자, 이제 연습이 끝났습니다! 지금부터 본격적으로 시작하겠습니다.")
                        self.ptt.tts.wait()

                        
                        print("  - ⌛ 본 게임 퀴즈 준비 완료 여부 확인 중...")
                        quiz_fetch_thread.join(timeout=5.0)

                        generated_quizzes = quiz_result_container

                        self.ptt._speak_and_subtitle("마지막까지 살아남으시는 분께는 특별한 상품을 드릴게요!")
                        self.ptt.tts.wait()
                        is_main_game_started = True

                    is_current_round_crazy = main_game_round_counter in crazy_mode_quizzes

                    if is_current_round_crazy:
                        if not is_crazy_mode_active:
                            print(f"미친 난이도 퀴즈 출제! (본 게임 {main_game_round_counter + 1} 라운드)")
                            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("ANGRY")
                            self.ptt._speak_and_subtitle("후후후... 난이도 상승! 후후후... 이번엔 정말 어려울 거다...")
                            self.ptt.tts.wait()
                            is_crazy_mode_active = True 

                        quiz_data = crazy_mode_quizzes[main_game_round_counter]

                    else:
                        if is_crazy_mode_active:
                            print("일반 난이도로 돌아갑니다.")
                            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
                            is_crazy_mode_active = False

                        if generated_quizzes:
                            quiz_data = generated_quizzes.pop(0)
                            print(f"  - 사전 생성 퀴즈 사용: {quiz_data}")

                        else:
                            print(" - Gemini API 실시간 새 퀴즈를 생성합니다.")
                            quiz_prompt = (
                                "간단한 상식 OX 퀴즈를 한국어로 하나만 만들어줘. "
                                "이전에 출제했던 문제와는 다른 새로운 주제로 내줘."
                                "출력은 반드시 다음 JSON 형식이어야 해. 다른 설명은 절대 추가하지 마.\n"
                                '{ "question": "<퀴즈 질문>", "answer": "O 또는 X" }'
                            )
                            try:
                                quiz_response = genai.GenerativeModel(self.MODEL_NAME).generate_content(
                                    quiz_prompt, 
                                    generation_config={"response_mime_type": "application/json"}
                                )
                                raw_json = _extract_text(quiz_response)
                                quiz_data = json.loads(raw_json)
                                print(f" - 생성된 퀴즈: {quiz_data}")
                            except Exception as e:
                                print(f" - 퀴즈 생성 실패: {e}. 폴백 퀴즈를 사용합니다.")
                                quiz_data = { "question": "사람은 코로 숨 쉬고 입으로도 숨 쉴 수 있다.", "answer": "O" }

                    main_game_round_counter += 1

                if not is_predefined:
                    if not is_first_round:
                        self.ptt._speak_and_subtitle("자, 다음 문제입니다!")
                        self.ptt.tts.wait()
                
                    if random.random() < 0.5:
                        thinking_phrases = [
                            "음... 어떤 문제를 내볼까?",
                            "히히 이거 재미있겠다.",
                            "이번에는 조금 어려울 수도 있어요."
                            "과연 맞출 수 있을까요?"
                            "인간에겐 너무 어려웠나? 쉽게 갈까요?"
                        ]

                        self.ptt._speak_and_subtitle(random.choice(thinking_phrases))
                        self.ptt.tts.wait() 
                
                self.ptt._speak_and_subtitle(quiz_data["question"])
                self.ptt.tts.wait()
                self.ptt._speak_and_subtitle("O는 오른쪽에, X는 왼쪽에 서주세요.")
                self.ptt.tts.wait()
                for i in range(5, 0, -1):
                    self.ptt._speak_and_subtitle(str(i))
                    time.sleep(0.1)
                self.ptt.tts.wait()

                command_to_send = {
                    "command": "START_OX_QUIZ" if is_first_round else "NEXT_ROUND",
                    "answer": quiz_data["answer"],
                    "is_predefined": is_predefined
                }
                self.ptt.ox_command_q.put(command_to_send)
                is_first_round = False

                try:
                    round_result = self.ptt.ox_result_q.get(timeout=35)
                    print(f"OX 퀴즈 라운드 결과 수신: {round_result}")

                    message_to_speak = round_result.get("message", "결과를 처리 중입니다.")
                    winner_count = round_result.get("winner_count", 0)
                    is_predefined_from_worker = round_result.get("is_predefined", False)
                    
                    time.sleep(1)
                    correct_answer_text = f"정답은 {quiz_data['answer']} 였습니다!"
                    self.ptt._speak_and_subtitle(correct_answer_text)
                    self.ptt.tts.wait()
                    
                    if is_predefined and quiz_data.get("explanation"):
                        self.ptt._speak_and_subtitle(quiz_data["explanation"])
                        self.ptt.tts.wait()

                    self.ptt._speak_and_subtitle(message_to_speak)
                    self.ptt.tts.wait()
                    
                    if is_predefined_from_worker:
                        time.sleep(2)
                        continue
                    
                    if winner_count > 1:
                        print("  - 😊 정답자가 있어 HAPPY 표정으로 변경")
                        if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY")
                        
                        time.sleep(3)
                        continue

                    elif winner_count == 1:
                        print("  - 🎉 최종 우승! HAPPY 표정으로 변경")
                        if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY")
                        time.sleep(10)
                        is_game_over = True

                    else: # winner_count == 0
                        print("  - 😥 정답자가 없어 SAD 표정으로 변경")
                        if self.ptt.emotion_queue: self.ptt.emotion_queue.put("SAD")
                        time.sleep(10)
                        is_game_over = True

                except queue.Empty:
                    print("OX 퀴즈 시간 초과. 워커로부터 결과를 받지 못했습니다.")
                    self.ptt._speak_and_subtitle("이런, 시간 안에 결과를 받지 못했어요. 게임을 종료합니다.")
                    is_game_over = True
            
            self.ptt.tts.wait()
            self.ptt._speak_and_subtitle("최후의 생존자와 가위바위보 게임을 진행할게요!... 만약 여기서 이기시면 어마무시한 선물을 드리도록 하겠습니다!... 하지만 패배하시면 벌칙을 받게 될거에요!... 마음의 준비가 되시면 개발자에게 가위바위보라고 말씀해주세요! ")

        finally:
            if self.ptt.shared_state:
                self.ptt.shared_state['mode'] = 'tracking'
            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")

    def run_rps_game(self):
        """가위바위보 게임 실행 로직 (gemini_api.py에서 이동)"""
        print("💡 의도: ROCK PAPER SCISSORS GAME")
        starts_dance = False

        try:
            if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")
            self.ptt._speak_and_subtitle("가위바위보 게임을 시작할게요. 잠시후 당신의 손동작을 보여주세요")
            time.sleep(1)
            final_game_result = ""

            while True: 
                if self.ptt.emotion_queue: self.ptt.emotion_queue.put("RESET_SLEEPY_TIMER")
                self.ptt.rps_command_q.put("START_GAME")
                if self.ptt.emotion_queue: self.ptt.emotion_queue.put("THINKING")
                self.ptt._speak_and_subtitle("준비하시고...")
                self.ptt.tts.wait()

                if callable(self.ptt.play_rps_motion_cb):
                    threading.Thread(target=self.ptt.play_rps_motion_cb, daemon=True).start()

                self.ptt._speak_and_subtitle("가위! 바위!")
                self.ptt._speak_and_subtitle("보!")
                self.ptt.tts.wait()

                game_result = ""
                try:
                    game_result = self.ptt.rps_result_q.get(timeout=20)
                    print(f"게임 결과 수신: {game_result}")
                
                except queue.Empty:
                    print("게임 시간 초과. 제스처를 인식하지 못했습니다.")
                    game_result = "아고! 실수로 눈을 감아서 인식을 못했어요. 죄송해요."

                if "아고! 실수로 눈을" in game_result:
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("CLOSE")
                elif "당신이 이겼어요" in game_result:
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("SAD")
                elif "제가 이겼네요" in game_result:
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("HAPPY")
                elif "비겼네요" in game_result:
                    if self.ptt.emotion_queue: self.ptt.emotion_queue.put("SURPRISED")
                    
                time.sleep(2)

                if "비겼" in game_result:
                    self.ptt._speak_and_subtitle(f"{game_result} 다시 한 번 할게요!")
                    self.ptt.tts.wait()                  
                    time.sleep(2)
                    continue

                elif "아고! 실수로 눈을" in game_result:
                    self.ptt._speak_and_subtitle("아고! 실수로 눈을 감아서 인식을 못했어요. 죄송해요. 다시 한 번 할게요!")
                    self.ptt.tts.wait()                  
                    time.sleep(2)
                    continue

                elif "이겼" in game_result:
                    if "제가 이겼네요"  in game_result:
                        self.ptt._speak_and_subtitle(f"{game_result} 제가 이겼으니 벌칙을 받아야죠! 저랑 같이 춤춰 주세요")
                    else:
                        self.ptt._speak_and_subtitle(f"{game_result} 까비! 벌칙을 피하셨네요. 제가 춤추는거 보여드릴게요.")
                    
                    self.ptt.tts.wait()

                    print("💡 게임 결과에 따라 DANCE START 의도 실행")

                    if callable(self.ptt.start_dance_cb):
                        self.ptt.start_dance_cb()
                        starts_dance = True
                    break

                else:
                    self.ptt._speak_and_subtitle("또 하고 싶으시면 '가위바위보'라고 말해주세요.")
                    break
        finally:
            if not starts_dance:
                # raise_busy_signal()은 _transcribe_then_chat에서 호출되었고,
                # 춤으로 이어지지 않으므로 여기서 lower_busy_signal()을 호출해야 합니다.
                self.ptt.lower_busy_signal()
            
            # model_text는 _transcribe_then_chat의 지역 변수이므로 여기서는 설정하지 않습니다.
            # final_game_result는 이 함수의 지역 변수이므로 괜찮습니다.
            print(f"게임 종료. 최종 결과: {final_game_result}")
            if not starts_dance:
                if self.ptt.emotion_queue: self.ptt.emotion_queue.put("NEUTRAL")