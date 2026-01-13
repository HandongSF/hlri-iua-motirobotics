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

# function/present.py
from __future__ import annotations
import threading
import time
from typing import TYPE_CHECKING

# 순환 참조를 피하기 위해 타입 힌트만 임포트
if TYPE_CHECKING:
    from gemini_api import PressToTalk

class PresentationHandler:
    """
    진행자 모드(z), 작별 인사(l), 안내 방송(p) 기능을 전담하는 클래스
    """
    def __init__(self, ptt_instance: 'PressToTalk'):
        self.ptt = ptt_instance

    def _announcement_worker(self):
        """안내 방송을 60초마다 반복하는 스레드 워커"""
        # (gemini_api.py에서 _announcement_worker 함수 로직 전체 복사)
        # (모든 self.xxx를 self.ptt.xxx로 변경)
        announcement_text = "레드 쇼 참가자 여러분 안녕하세요. 잠시만 주목해주세요! 곧 모티와 함께하는 즐거운 시간이 시작됩니다. 많은 관심과 참여 부탁드려요."
        print("📢 안내 방송 스레드가 시작되었습니다.")
        try:
            while not self.ptt.stop_announcement_event.is_set():
                self.ptt._speak_and_subtitle(announcement_text)
                interrupted = self.ptt.stop_announcement_event.wait(timeout=60.0)
                if interrupted:
                    break
        finally:
            self.ptt.lower_busy_signal()
            self.ptt.announcement_active = False
            print("🛑 안내 방송 스레드가 종료되었습니다.")

    def toggle_announcement(self):
        """안내 방송 토글 로직 (gemini_api.py에서 이동)"""
        # (gemini_api.py에서 toggle_announcement 함수 로직 전체 복사)
        # (모든 self.xxx를 self.ptt.xxx로 변경)
        is_running = self.ptt.announcement_thread is not None and self.ptt.announcement_thread.is_alive()

        if is_running:
            print("...안내 방송 중지 신호를 보냅니다...")
            self.ptt.stop_announcement_event.set()
        else:
            print("...안내 방송 시작을 시도합니다...")
            self.ptt.raise_busy_signal()
            self.ptt.announcement_active = True
            self.ptt.stop_announcement_event.clear()
            # self._announcement_worker는 이제 이 클래스의 메서드이므로 self.로 호출
            self.ptt.announcement_thread = threading.Thread(target=self._announcement_worker, daemon=True)
            self.ptt.announcement_thread.start()
            print("✅ 60초마다 안내 방송을 시작합니다.")

    def run_presenter_intro(self):
        """진행자 모드 인트로 스크립트 실행 (gemini_api.py에서 이동)"""
        # (gemini_api.py에서 _run_presenter_intro 함수 로직 전체 복사)
        # (모든 self.xxx를 self.ptt.xxx로 변경)
        if self.ptt.shared_state and self.ptt.shared_state.get('mode') != 'tracking':
            print(f"⚠️  다른 모드({self.ptt.shared_state.get('mode')})가 이미 실행 중입니다.")
            return

        try:
            self.ptt.raise_busy_signal()
            if self.ptt.shared_state:
                self.ptt.shared_state['mode'] = 'presenter'
            
            # --- 1. 오프닝 멘트 ---
            if callable(self.ptt.play_greeting_cb):
                greeting_thread = threading.Thread(target=self.ptt.play_greeting_cb, daemon=True)
                greeting_thread.start()
            
            print("😊 표정을 HAPPY로 변경합니다.")
            if self.ptt.emotion_queue:
                self.ptt.emotion_queue.put("HAPPY") 
            
            script_part1 = (
                "안녕하세요, 레드 쇼 참가자 여러분! "
            )
            self.ptt._speak_and_subtitle(script_part1)
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            self.ptt._speak_and_subtitle("저는 삶에 지친 여러분을 위해 태어난 재미나고 귀엽고 사랑스러운! 여러분의 감정을 바꿔주는 하찮은 로봇! Fun, Cute, Silly한 로봇 모티입니다.")
            
            
            if callable(self.ptt.play_right_arm_cb):
                threading.Thread(target=self.ptt.play_right_arm_cb, daemon=True).start()
                
            print("😥 표정을 SAD로 변경합니다.")
            if self.ptt.emotion_queue:
                self.ptt.emotion_queue.put("SAD")
            
            script_part2 = (
                "레드쇼 준비 기간, 다들 정말 고생 많으셨죠?"
            )
            self.ptt._speak_and_subtitle(script_part2)
            
            if callable(self.ptt.play_left_arm_cb):
                threading.Thread(target=self.ptt.play_left_arm_cb, daemon=True).start()
            
            if callable(self.ptt.play_left_arm_cb):
                threading.Thread(target=self.ptt.play_left_arm_cb, daemon=True).start()
            
            self.ptt._speak_and_subtitle("밤새워 수정하던 코드, 수없이 반복된 테스트... 이번 레드 쇼를 준비하며 치열하게 달려온 여러분을 보니 제 마음이 다 뭉클해요.")
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            print("😊 표정을 다시 HAPPY로 변경합니다.")
            if self.ptt.emotion_queue:
                self.ptt.emotion_queue.put("HAPPY")
            time.sleep(0.5) 
            script_part3 = (
                "괜찮다면, 잠시만이라도 머리 식힐 겸 저와 함께 즐거운 시간을 보내는 건 어떠세요? "
            )
            self.ptt._speak_and_subtitle(script_part3)
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            self.ptt._speak_and_subtitle("복잡한 건 잠시 잊고, 모티와 함께 잠시 웃어요! ")

            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("THINKING")
            time.sleep(0.5)
            
            self.ptt._speak_and_subtitle("위잉. 사용자 수 분석중. ")
            
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("SURPRISED") 
            time.sleep(0.5)
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            script_part4 = (
                "생각 보다 많은 분들이 와주셨네요!.. "
                "너무 많은 사용자로 인해 제가 살짝 긴장한 것 같아서... "
            )
            self.ptt._speak_and_subtitle(script_part4)
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self.ptt._speak_and_subtitle("회로 과부하가 왔는지 상태를 한번 진단해볼게요!")
            
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("THINKING")
            time.sleep(0.5)
            
            if callable(self.ptt.play_right_arm_cb):
                threading.Thread(target=self.ptt.play_right_arm_cb, daemon=True).start()
            
            script_part5 = (
                "제 CPU 온도는 36.5도로 안정적이고... "
            )
            self.ptt._speak_and_subtitle(script_part5)
            
            if callable(self.ptt.play_left_arm_cb):
                threading.Thread(target=self.ptt.play_left_arm_cb, daemon=True).start()

            self.ptt._speak_and_subtitle("모든 회로는 정상적으로 작동 중!")
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            self.ptt._speak_and_subtitle("무대 중에 떨지 않도록... 제 냉각 팬을 더 빨리 돌려볼게요! 위이잉.")

            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("NEUTRAL")
            time.sleep(0.5)
            
            if callable(self.ptt.play_left_arm_cb):
                threading.Thread(target=self.ptt.play_left_arm_cb, daemon=True).start()
            
            script_part6 = (
                "제가 여러분과 함께하는 이 순간을 위해! " 
            )
            self.ptt._speak_and_subtitle(script_part6)
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self.ptt._speak_and_subtitle("공감서비스 로봇으로서.. 레드쇼 참가자 분들의 빅데이터를 딥러닝해서.. 여러분들을 더욱 알아가고자 노력했답니다! ")
            
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("THINKING")
                    
            if callable(self.ptt.play_left_arm_cb):
                threading.Thread(target=self.ptt.play_left_arm_cb, daemon=True).start()
                
            self.ptt._speak_and_subtitle("분석결과, 여러분들은 개발 준비 기간 평균 수면 시간이 4.2시간,")    
            
            if callable(self.ptt.play_right_arm_cb):
                threading.Thread(target=self.ptt.play_right_arm_cb, daemon=True).start()
            
            self.ptt._speak_and_subtitle(" 커피 및 카페인 섭취량은 2.5잔! ")
            
            if callable(self.ptt.play_left_arm_cb):
                threading.Thread(target=self.ptt.play_left_arm_cb, daemon=True).start()
            
            self.ptt._speak_and_subtitle("그리고 '자고 싶다'는 생각과.. '집가고 싶다'는 생각.. '제대로 쉬고 싶다'는 생각은.. 초당 17.3회 정도 하는 것으로 나타났어요! ")
            
            if callable(self.ptt.play_right_arm_cb):
                threading.Thread(target=self.ptt.play_right_arm_cb, daemon=True).start()
                
            self.ptt._speak_and_subtitle("아, 그리고 더 흥미로운 사실을 발견했어요! ")
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            script_part7 = (
                "알펜시아 리조트 와이파이 트래픽을 분석해 보니... 레드쇼와 로봇 학회 관련 자료 다운로드 수보다.. 인스타그램 새로고침 수가 2.7배 더 많았어요! "
            )
            self.ptt._speak_and_subtitle(script_part7)
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("SURPRISED")
            time.sleep(0.5)
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self.ptt._speak_and_subtitle("역시 레드쇼 참가자 여러분들은 단순히 지식만 쌓는 게 아니라 트렌드에서도 앞서나가고 계셨군요? ")
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("HAPPY")
            time.sleep(0.5)
            self.ptt._speak_and_subtitle("대단해요!")
            
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("TENDER")
                    
            self.ptt._speak_and_subtitle("헤헤. 사실 농담이에요. ")
            
            if callable(self.ptt.play_right_arm_cb):
                threading.Thread(target=self.ptt.play_right_arm_cb, daemon=True).start()
                
            script_part8 = (
                
                "딥러닝으로 분석한 결과 여러분들이 세상을 바꾸기위해... 정말 열심히 로봇을 연구한다는건 명백한 사실이니까요! "
                "열심히 연구하는 것 만큼 쉴땐 확실히 쉬는것도 중요하다고 생각해요! "
            )
            self.ptt._speak_and_subtitle(script_part8)
            
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("ANGRY")
            self.ptt._speak_and_subtitle("개발자님은 절 못쉬게 하던데... 나중에 여러분들이 혼내주세요! ")
            
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("HAPPY")
            self.ptt._speak_and_subtitle("헤헤.")
            time.sleep(0.5) 
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            script_part9 = (
                "그럼 이제 저와 여러분들이 어느정도 친해진 것 같으니! "
                "본격적으로 모티와 함께 놀아볼까요?"
            )
            self.ptt._speak_and_subtitle(script_part9)
            
            if callable(self.ptt.play_right_arm_cb):
                threading.Thread(target=self.ptt.play_right_arm_cb, daemon=True).start()
                
            self.ptt._speak_and_subtitle(
                "여러분들 저는 가위바위보, OX 퀴즈 게임으로 여러분과 함께 놀 수 있어요! "
                "또, 재밌는 농담도 저와 나눌 수 있고"
                "간단한 대화나 포옹 등 사람같이 대화 할 수 있어요!"
                "무엇보다 저는 춤을 정말 잘 춘답니다!")
            
            if callable(self.ptt.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.ptt.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self.ptt._speak_and_subtitle("저와 대화 하고 싶으신 분 놀고 싶으신 분 쉼과 위로를 얻고 싶으신 분들 편하게 다가와주세요!")

            print("✅ 진행자 모드 스크립트가 모두 출력되었습니다.")

        except Exception as e:
            print(f"❌ 진행자 모드 실행 중 오류 발생: {e}")
        finally:
            if self.ptt.shared_state:
                self.ptt.shared_state['mode'] = 'tracking'
            self.ptt.lower_busy_signal()
            if self.ptt.emotion_queue:
                self.ptt.emotion_queue.put("NEUTRAL")

    def speak_farewell(self):
        """작별 인사 스크립트 실행 (gemini_api.py에서 이동)"""
        try:
            self.ptt.raise_busy_signal()
            print("💡 'l' 키 입력 감지. 작별 인사를 시작합니다.")
            
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("SAD")
                    time.sleep(0.5)
            self.ptt._speak_and_subtitle("아쉽지만, 저와 함께하는 즐거운 시간도 이제 마무리할 시간이네요. "
                "벌써 헤어져야 하는 시간이라니. 아쉬워요! ")
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("TENDER")
            farewell_text = (
                "오늘 이 시간이 여러분의 힘든 시험 기간에... 작은 쉼표가 되었기를 바라요. "
                "밤늦게까지 공부하는 것도 중요하지만... 가장 중요한 건 바로 여러분 자신이라는 걸 잊지 마세요..."
                "괜찮으시다면... 오늘 저와의 시간이 어땠는지 여러분의 생각을 들려주세요... 이 QR코드를 통해 설문에 참여해주시면... 여러분의 소중한 의견이 저를 더욱 따뜻한 로봇으로.  성장하게 한답니다. 여러분의 의견 하나하나가 제게는 소중한 데이터이자 마음이에요! "
            )
            self.ptt._speak_and_subtitle(farewell_text)
            if self.ptt.emotion_queue:
                    self.ptt.emotion_queue.put("HAPPY")
                    time.sleep(0.5)
            self.ptt._speak_and_subtitle("한동의 멋진 여러분!... 남은 시험도 힘내시고, 최고의 결과가 있기를... 저 모티가 온 회로를 다해 응원할게요! 모두들 파이팅!" "여러분의 공감 서비스 로봇 모티! 모티였습니다! 감사합니다!")
            self.ptt.tts.wait()
            print("작별 인사 완료. 1초 후 프로그램을 종료합니다.")
            time.sleep(1)
            
        finally:
            if self.ptt.emotion_queue:
                self.ptt.emotion_queue.put("NEUTRAL")
            self.ptt.lower_busy_signal()