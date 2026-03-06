# ============================================================
#Licensed to the Apache Software Foundation (ASF) under one
#or more contributor license agreements.  See the NOTICE file
#distributed with this work for additional information
#regarding copyright ownership.  The ASF licenses this file
#to you under the Apache License, Version 2.0 (the
#"License"); you may not use this file except in compliance
#with the License.  You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
# ============================================================
# function/profile_manager.py

from __future__ import annotations
import os
import json
import re
import threading
import google.generativeai as genai
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gemini_api import PressToTalk

from function.utils import _get_relative_time_str, _extract_text, SYSTEM_INSTRUCTION

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DB_FILE = os.path.join(BASE_DIR, "user_profiles.json")

class ProfileManager:
    """
    사용자 프로필(DB) 초기화, 로드, 저장을 전담하는 클래스
    """
    def __init__(self, ptt_instance: 'PressToTalk'):
        self.ptt = ptt_instance
        self.MODEL_NAME = ptt_instance.MODEL_NAME
        # 인스턴스 변수로 DB 파일 경로 저장 (절대 경로)
        self.db_file = PROFILE_DB_FILE
        self.lock = threading.Lock() # 파일 동시 접근 방지용 Lock

    def init_db(self):
        """JSON 프로필 DB 파일이 없으면 빈 객체로 생성합니다."""
        if not os.path.exists(self.db_file):
            print(f"ℹ️ 프로필 DB 파일({self.db_file})이 없어 새로 생성합니다.")
            try:
                with open(self.db_file, "w", encoding="utf-8") as f:
                    json.dump({}, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print(f"❌ 프로필 DB 파일 생성 실패: {e}")

    def _load_all_profiles(self) -> dict:
        """DB 파일에서 모든 프로필 데이터를 읽어옵니다."""
        try:
            if os.path.exists(self.db_file):
                with open(self.db_file, "r", encoding="utf-8") as f:
                    try:
                        return json.load(f)
                    except json.JSONDecodeError:
                        return {} # 파일이 깨졌거나 비어있으면 빈 딕셔너리 반환
            return {}
        except Exception as e:
            print(f"❌ 프로필 로드 실패: {e}")
            return {}

    def _save_to_file(self, data: dict):
        """딕셔너리 데이터를 DB 파일에 씁니다."""
        try:
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"❌ 프로필 저장 실패: {e}")

    def load_profile_for_chat(self, name: str):
        """
        사용자 이름을 기반으로 '요약된 사실'을 로드하여 시스템 프롬프트에 주입하고,
        Gemini Chat 세션을 새로운 컨텍스트로 재초기화합니다.
        """
        if not name: return

        print(f"⏳ {name}님의 프로필 로드를 시도합니다...")
        
        chat_summary = "아직 기록된 내용이 없습니다."
        last_seen_str = "기록 없음" 
        relative_time_str = "기록 없음" 

        with self.lock:
            data = self._load_all_profiles()

            if name in data:
                chat_summary = data[name].get("chat_summary", "아직 기록된 내용이 없습니다.")
                last_seen_iso = data[name].get("last_seen")
                
                if last_seen_iso:
                    try:
                        last_seen_dt_obj = datetime.fromisoformat(last_seen_iso)
                        dt_now = datetime.now()
                        
                        relative_time_str = _get_relative_time_str(last_seen_dt_obj, dt_now) 
                        last_seen_str = last_seen_dt_obj.strftime('%Y년 %m월 %d일 %H시 %M분')
                    except ValueError:
                        pass 
                
                print(f"✅ {name}님의 프로필을 성공적으로 로드했습니다. (마지막 대화: {relative_time_str})")
            
            else:
                print(f"ℹ️ {name}님의 프로필이 없습니다. 새로 생성합니다.")
                # 신규 사용자라면 즉시 빈 프로필을 생성하여 저장합니다.
                data[name] = {
                    "chat_summary": "신규 사용자입니다.",
                    "last_seen": datetime.now().isoformat()
                }
                self._save_to_file(data)
        
        # 1. PTT 인스턴스의 초기값 설정
        self.ptt.current_user_name = name
        self.ptt.initial_chat_summary = chat_summary
        self.ptt.initial_last_seen_str = last_seen_str 
        current_time_str = datetime.now().strftime('%Y년 %m월 %d일 %A')

        # 2. 강화된 시스템 명령어 생성
        enhanced_system_instruction = (
            SYSTEM_INSTRUCTION +
            f"\n\n--- 현재 시간 ---\n"
            f"오늘은 {current_time_str}입니다. 이 시간 정보를 바탕으로 '어제', '오늘' 등을 정확히 인지하세요."
            "\n\n--- 중요 기억 (필독!) ---\n"
            f"당신은 지금 **'{name}'님**과 대화하고 있습니다. **사용자의 이름을 잊지 말고 항상 기억하세요.**\n"
            f"다음은 '{name}'님에 대해 당신이 기억하고 있는 중요한 사실들입니다 ( {relative_time_str} 기준):\n"
            f"{chat_summary}\n"
            "--- 중요 기억 활용 규칙 ---\n"
            "1. 사용자의 질문에 답하기 전, 항상 [중요 기억] 섹션에 관련 정보가 있는지 먼저 확인하세요.\n"
            f"2. (예시) 사용자가 '오늘 뭐할까?'라고 물었고, [중요 기억]에 '- {current_time_str.split(' ')[0]} 5시까지 공부할 예정'이라고 적혀있다면, '기억하기로는 오늘 5시까지 공부하실 계획이 있으셨어요.'라고 먼저 알려주세요.\n"
            "3. [중요 기억]은 대화 주제와 '직접적으로 관련이 있을 때만' 자연스럽게 언급하세요. 뜬금없이 반복해서 말하지 마세요.\n" 
            "4. [!! 중요 대화 규칙 !!] 기억 속의 사실을 언급할 때, '2025년 11월 17일'처럼 [절대 날짜]를 직접 말하지 마세요.\n"
            " - 대신, [현재 시간]을 기준으로 '어제', '며칠 전에', '예전에' 같은 [상대 시간]으로 자연스럽게 표현하세요.\n"
            " - (예: [중요 기억]에 '- 2025년 11월 17일: 개구리를 싫어함'이라고 적혀있고 오늘이 11월 18일이라면, '아, 맞다. 어제 개구리 싫어한다고 하셨죠!'라고 말하세요.)\n"
            "--- 중요 기억 끝 ---"
        )
        
        # 3. Chat 세션을 새 시스템 프롬프트로 재초기화
        self.ptt.chat = genai.GenerativeModel(
            self.MODEL_NAME, 
            system_instruction=enhanced_system_instruction
        ).start_chat(history=[])

        print(f"🧠 Gemini Chat 세션을 {name}님 프로필로 성공적으로 재설정했습니다.")

    def batch_update_summary(self, conversation_log: str):
        """
        쌓여있던 대화 로그 문자열을 한 번에 받아서 요약하고, 프로필(기억)을 업데이트합니다.
        대화 세션이 끝날 때(Sleepy 모드 진입, 종료 등) 호출됩니다.
        """
        if not conversation_log or not self.ptt.current_user_name:
            print("ℹ️ 저장할 대화 내용이 없거나 사용자 이름이 없어 저장을 건너뜁니다.")
            return

        name = self.ptt.current_user_name
        print(f"🧠 [Memory] {name}님과의 대화 내용을 정리하여 저장합니다...")

        # 1. 기존 요약 정보 가져오기
        old_summary = self.ptt.initial_chat_summary
        last_seen_str = self.ptt.initial_last_seen_str
        
        # 2. 날짜 계산
        current_time_dt = datetime.now()
        one_week_ago_dt = current_time_dt - timedelta(days=7)
        
        current_time_str = current_time_dt.strftime('%Y년 %m월 %d일 %H시 %M분')
        current_date_str_for_chat = current_time_dt.strftime('%Y년 %m월 %d일 %A')
        one_week_ago_str = one_week_ago_dt.strftime('%Y년 %m월 %d일')

        try:
            # 3. 요약기 프롬프트 구성
            summarizer_prompt = (
                f"당신은 대화 내용을 바탕으로 사용자의 프로필을 관리하는 AI입니다.\n"
                f"현재 시간은 [ {current_time_str} ]입니다.\n"
                f"!! 삭제 기준일은 [ {one_week_ago_str} ]입니다. (오늘로부터 1주일 전)\n"
                f"아래의 [기존 사실] ( {last_seen_str} 기준)을 [이번 세션 전체 대화] ( {current_time_str} 에 종료됨)의 내용으로 업데이트하여, [새로운 사실 목록]을 만드세요.\n\n"
                "규칙:\n"
                "1. 대화에서 '사용자'에 대한 '중요한 개인 정보'만 추출합니다.\n"
                "2. 단순한 인사나 잡담은 무시합니다.\n"
                "3. '새로운 사실 목록'은 항상 간결한 불렛 포인트(-)로 작성합니다.\n"
                "4. [이번 세션 전체 대화]에서 추출할 새 사실이 없다면, [기존 사실]을 (삭제 규칙 적용 후) 그대로 출력합니다.\n"
                "5. [!!기억 삭제 규칙!!] 1주일이 지난 일상적인 잡담이나 일회성 상태는 삭제하되, 핵심 개인정보(이름, 생일, 직업, 성격, 취향, 게임, 운동, 좋아하는 것, 취미, 장기 목표 등)는 1주일이 지나도 절대 삭제하지 말고 영구적으로 유지하세요. (단, 8번 규칙에 의해 정보가 갱신되어 덮어쓰는 경우는 예외입니다.)\n"
                "6. [!!날짜/사실 분리 규칙!!] 영구적 사실은 날짜를 적지 않고(예: - 전공: 미술), 특정 시점 사건이나 상태는 기준일을 포함하세요(예: - 수업 듣는 중 (상태, 2026년 03월 06일)).\n"
                "7. [!!메아리 방지 규칙!!] 대화 로그에서 AI(모티)가 사용자의 과거 취향이나 기억을 단순히 '대답(회상)'해준 내용은 절대 새로운 정보로 추출하지 마세요. 오직 사용자가 '새롭게 직접 말한' 정보만 추출해야 합니다. 이미 [기존 사실]에 있는 내용을 재확인한 대화라면 기존 사실의 날짜를 오늘로 갱신하지 말고 예전 그대로 유지하세요.\n"
                "8. [!!정보 갱신(덮어쓰기) 규칙!!] 사용자의 직업, 취향, 관심사, 목표 등 기존 [기존 사실]의 내용이 새롭게 변경되었거나 과거 사실과 충돌하는 경우(예: 학생 -> 취업), 옛날 정보와 새 정보를 중복해서 나열하지 말고 반드시 **가장 최신 정보 하나로 덮어쓰기(Overwrite)** 하세요.\n"
                "9. [!!출력 규칙!!] 대답을 시작할 때 '네, 업데이트하겠습니다' 등의 인사말이나 부연 설명을 절대 하지 말고, 오직 업데이트된 불렛 포인트(-) 목록만 반환하세요.\n\n"
                f"[기존 사실 ( {last_seen_str} 기준)]\n{old_summary}\n\n"
                f"[이번 세션 전체 대화 ( {current_time_str} 에 종료됨)]\n"
                f"{conversation_log}\n\n"
                "[새로운 사실 목록] (1주일 이내의 정보 + 핵심 정보만 포함)\n"
            )

            # Gemini 호출
            summarizer_model = genai.GenerativeModel(self.MODEL_NAME)
            response = summarizer_model.generate_content(summarizer_prompt)
            new_summary = _extract_text(response)

            # 4. 파일(DB) 저장 (Lock 사용)
            with self.lock:
                data = self._load_all_profiles()
                
                if name not in data:
                    data[name] = {}
                
                data[name]["chat_summary"] = new_summary
                data[name]["last_seen"] = datetime.now().isoformat()
                
                self._save_to_file(data)

            # 5. 메모리(인스턴스) 상태 업데이트
            self.ptt.initial_chat_summary = new_summary
            self.ptt.initial_last_seen_str = current_time_str

            print(f"✅ {name}님의 기억(프로필) 업데이트 완료!")

        except Exception as e:
            print(f"❌ [Memory] 기억 업데이트 실패: {e}")

    def save_profile_at_exit(self):
        """
        신규 사용자 등록 직후 등, 강제로 현재 프로필을 저장해야 할 때 호출합니다.
        (batch_update_summary는 대화 로그가 있을 때만 저장하므로, 이름만 등록된 경우 이 함수가 필요합니다.)
        """
        current_user = self.ptt.shared_state.get('current_user_name')
        if not current_user or current_user == "Unknown":
            return

        print(f"💾 {current_user}님의 기본 프로필(접속기록) 저장을 시도합니다...")
        
        with self.lock:
            data = self._load_all_profiles()
            
            # 신규 사용자이거나 정보가 없으면 기본값 생성
            if current_user not in data:
                data[current_user] = {
                    "chat_summary": "신규 등록된 사용자입니다.",
                    "created_at": datetime.now().isoformat()
                }
            
            # 마지막 접속 시간 갱신
            data[current_user]['last_seen'] = datetime.now().isoformat()
            
            self._save_to_file(data)
            print(f"✅ {current_user}님의 프로필이 {self.db_file}에 저장되었습니다.")