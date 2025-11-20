# function/profile_manager.py
from __future__ import annotations
import os
import json
import re
import google.generativeai as genai
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

# 순환 참조를 피하기 위해 타입 힌트만 임포트
if TYPE_CHECKING:
    from gemini_api import PressToTalk

from function.utils import _get_relative_time_str, _extract_text, SYSTEM_INSTRUCTION

class ProfileManager:
    """
    사용자 프로필(DB) 초기화, 로드, 저장을 전담하는 클래스
    """
    def __init__(self, ptt_instance: 'PressToTalk'):
        self.ptt = ptt_instance
        self.MODEL_NAME = ptt_instance.MODEL_NAME

    def init_db(self):
        """JSON 프로필 DB 파일이 없으면 빈 객체로 생성합니다."""
        if not os.path.exists(self.ptt.profile_db_file):
            print(f"ℹ️ 프로필 DB 파일({self.ptt.profile_db_file})이 없어 새로 생성합니다.")
            try:
                with open(self.ptt.profile_db_file, "w", encoding="utf-8") as f:
                    json.dump({}, f)
            except Exception as e:
                print(f"❌ 프로필 DB 파일 생성 실패: {e}")

    def load_profile_for_chat(self, name: str):
        """사용자 이름을 기반으로 '요약된 사실'을 로드하여 시스템 프롬프트에 주입합니다."""
        print(f"⏳ {name}님의 프로필 로드를 시도합니다...")
        
        chat_summary = "아직 기록된 내용이 없습니다."
        last_seen_str = "기록 없음" # (요약기록용 절대 날짜)
        relative_time_str = "기록 없음" # (채팅 프롬프트용 상대 날짜)

        try:
            if os.path.exists(self.ptt.profile_db_file):
                with open(self.ptt.profile_db_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}

            if name in data:
                chat_summary = data[name].get("chat_summary", "아직 기록된 내용이 없습니다.")
                last_seen_iso = data[name].get("last_seen")
                
                if last_seen_iso:
                    try:
                        last_seen_dt_obj = datetime.fromisoformat(last_seen_iso)
                        dt_now = datetime.now()
                        
                        # 상대 시간 계산
                        relative_time_str = _get_relative_time_str(last_seen_dt_obj, dt_now) 
                        
                        # 기존 'last_seen_str'는 종료 시 요약기를 위해 절대 날짜 형식 유지
                        last_seen_str = last_seen_dt_obj.strftime('%Y년 %m월 %d일 %H시 %M분')
                    except ValueError:
                        pass # 파싱 실패 시 "기록 없음" 유지
                
                print(f"✅ {name}님의 프로필을 성공적으로 로드했습니다. (마지막 대화: {relative_time_str})")
                # [수정] 중복 인사 방지를 위해 하드코딩된 음성 출력 제거
                # Gemini가 생성한 reply가 대신 출력됩니다.
                # self.ptt._speak_and_subtitle(f"{name}님, 다시 만나서 반가워요!")
            
            else:
                print(f"ℹ️ {name}님의 프로필이 없습니다. 새로 생성합니다.")
                # [수정] 중복 인사 방지를 위해 제거
                # self.ptt._speak_and_subtitle(f"{name}님, 만나서 반가워요! 오늘부터 기억해둘게요.")
        
        except Exception as e:
            print(f"❌ 프로필 로드 실패: {e}")
            # [수정] 중복 인사 방지를 위해 제거
            # self.ptt._speak_and_subtitle(f"{name}님, 만나서 반가워요!")

        self.ptt.current_user_name = name
        self.ptt.initial_chat_summary = chat_summary
        self.ptt.initial_last_seen_str = last_seen_str 
        current_time_str = datetime.now().strftime('%Y년 %m월 %d일 %A')

        enhanced_system_instruction = (
            SYSTEM_INSTRUCTION +
            f"\n\n--- 현재 시간 ---\n"
            f"오늘은 {current_time_str}입니다. 이 시간 정보를 바탕으로 '어제', '오늘' 등을 정확히 인지하세요."
            "\n\n--- 중요 기억 (필독!) ---\n"
            f"당신은 지금 '{name}'님과 대화하고 있습니다.\n"
            f"다음은 '{name}'님에 대해 당신이 기억하고 있는 중요한 사실들입니다 ( {relative_time_str} 기준):\n"
            f"{chat_summary}\n"
            "--- 중요 기억 활용 규칙 ---\n"
            "1. 사용자의 질문에 답하기 전, 항상 [중요 기억] 섹션에 관련 정보가 있는지 먼저 확인하세요.\n"
            f"2. (예시) 사용자가 '오늘 뭐할까?'라고 물었고, [중요 기억]에 '- {current_time_str.split(' ')[0]} 5시까지 공부할 예정'이라고 적혀있다면, '기억하기로는 오늘 5시까지 공부하실 계획이 있으셨어요.'라고 먼저 알려주세요.\n"
            "3. [중요 기억]은 대화 주제와 '직접적으로 관련이 있을 때만' 자연스럽게 언급하세요. 뜬금없이 반복해서 말하지 마세요.\n" 
            "4. [!! 중요 대화 규칙 !!] 기억 속의 사실을 언급할 때, '2025년 11월 17일'처럼 [절대 날짜]를 직접 말하지 마세요.\n"
            "   - 대신, [현재 시간]을 기준으로 '어제', '며칠 전에', '예전에' 같은 [상대 시간]으로 자연스럽게 표현하세요.\n"
            "   - (예: [중요 기억]에 '- 2025년 11월 17일: 개구리를 싫어함'이라고 적혀있고 오늘이 11월 18일이라면, '아, 맞다. 어제 개구리 싫어한다고 하셨죠!'라고 말하세요.)\n"
            "--- 중요 기억 끝 ---"
        )
        
        self.ptt.chat = genai.GenerativeModel(
            self.MODEL_NAME, 
            system_instruction=enhanced_system_instruction
        ).start_chat(history=[])

    def update_summary_after_chat(self, user_text: str, ai_response: str):
        """
        단일 대화가 끝난 직후, 'self.chat.history'를 업데이트하고,
        다음 대화를 위해 시스템 프롬프트를 갱신합니다.
        """
        if not self.ptt.current_user_name:
            return 
        
        print(f"⏳ {self.ptt.current_user_name}님의 메모리(시스템 프롬프트) 업데이트 중...")

        try:
            # 1. 현재 대화 기록을 가져옴
            current_history = self.ptt.chat.history
            
            # 2. 기존 요약 정보 가져오기
            old_summary = self.ptt.initial_chat_summary
            last_seen_str = self.ptt.initial_last_seen_str
            current_time_dt = datetime.now()
            current_time_str = current_time_dt.strftime('%Y년 %m월 %d일 %H시 %M분')
            current_date_str_for_chat = current_time_dt.strftime('%Y년 %m월 %d일 %A')
            one_week_ago_dt = current_time_dt - timedelta(days=7)
            one_week_ago_str = one_week_ago_dt.strftime('%Y년 %m월 %d일')

            # 3. 요약기 프롬프트 생성
            summarizer_prompt = (
                f"당신은 대화 내용을 바탕으로 사용자의 프로필을 관리하는 AI입니다.\n"
                f"현재 시간은 [ {current_time_str} ]입니다.\n"
                f"삭제 기준일은 [ {one_week_ago_str} ]입니다.\n"
                f"아래의 [기존 사실] ( {last_seen_str} 기준)을 [방금 나눈 대화] ( {current_time_str} 발생)의 내용으로 업데이트하여, [새로운 사실 목록]을 만드세요.\n\n"
                "규칙:\n"
                "1. [방금 나눈 대화]에서 '사용자'에 대한 '중요한 개인 정보'만 추출합니다.\n"
                "2. 단순한 잡담('안녕')은 무시합니다.\n"
                "3. '새로운 사실 목록'은 간결한 불렛 포인트(-)로 작성합니다.\n"
                "4. [기존 사실]에서 1주일이 지난 정보는 삭제합니다. (단, 이름, 생일 등 핵심 정보 제외)\n"
                "5. '오늘' 같은 상대 시간 대신 'YYYY년 MM월 DD일'의 절대 날짜로 기록합니다.\n\n"
                f"[기존 사실 ( {last_seen_str} 기준)]\n{old_summary}\n\n"
                f"[방금 나눈 대화 ( {current_time_str} 에 발생)]\n"
                f"사용자: {user_text}\n"
                f"모티(AI): {ai_response}\n\n"
                "[새로운 사실 목록] (1주일 이내의 정보 + 핵심 정보만 포함)\n"
            )

            summarizer_model = genai.GenerativeModel(self.MODEL_NAME)
            response = summarizer_model.generate_content(summarizer_prompt)
            new_summary = _extract_text(response)
            
            # 4. self의 초기값만 업데이트
            self.ptt.initial_chat_summary = new_summary
            self.ptt.initial_last_seen_str = current_time_str 
            
            # 5. self.chat 객체를 새 요약 정보로 재-초기화
            enhanced_system_instruction = (
                SYSTEM_INSTRUCTION +
                f"\n\n--- 현재 시간 ---\n"
                f"오늘은 {current_date_str_for_chat}입니다."
                "\n\n--- 중요 기억 (필독!) ---\n"
                f"당신은 지금 '{self.ptt.current_user_name}'님과 대화하고 있습니다.\n"
                f"다음은 '{self.ptt.current_user_name}'님에 대해 당신이 기억하고 있는 중요한 사실들입니다 ( {current_time_str} 기준):\n"
                f"{new_summary}\n"
                "--- 중요 기억 활용 규칙 ---\n"
                "1. 사용자의 질문에 답하기 전, 항상 [중요 기억] 섹션에 관련 정보가 있는지 먼저 확인하세요.\n"
                f"2. (예시) 사용자가 '오늘 뭐할까?'라고 물었고, [중요 기억]에 '- {current_time_str.split(' ')[0]} 5시까지 공부할 예정'이라고 적혀있다면, '기억하기로는 오늘 5시까지 공부하실 계획이 있으셨어요.'라고 먼저 알려주세요.\n"
               "3. [중요 기억]은 대화 주제와 '직접적으로 관련이 있을 때만' 자연스럽게 언급하세요. 뜬금없이 반복해서 말하지 마세요.\n"
                "4. [!! 중요 대화 규칙 !!] 기억 속의 사실을 언급할 때, '2025년 11월 17일'처럼 [절대 날짜]를 직접 말하지 마세요.\n"
                "   - 대신, [현재 시간]을 기준으로 '어제', '며칠 전에', '예전에' 같은 [상대 시간]으로 자연스럽게 표현하세요.\n"
                "   - (예: [중요 기억]에 '- 2025년 11월 17일: 개구리를 싫어함'이라고 적혀있고 오늘이 11월 18일이라면, '아, 맞다. 어제 개구리 싫어한다고 하셨죠!'라고 말하세요.)\n"
                "--- 중요 기억 끝 ---"
            )
            
            self.ptt.chat = genai.GenerativeModel(
                self.MODEL_NAME, 
                system_instruction=enhanced_system_instruction
            ).start_chat(history=current_history)

            print(f"✅ {self.ptt.current_user_name}님의 메모리(시스템 프롬프트)가 업데이트되었습니다.")

        except Exception as e:
            print(f"❌ (실시간) 프로필 요약 업데이트 실패: {e}")
            
    def save_profile_at_exit(self):
        """
        프로그램 종료 시, self.chat.history에 누적된 전체 대화 내역을 바탕으로
        프로필 요약을 *한 번만* 업데이트하고 저장합니다.
        """
        if not self.ptt.current_user_name:
            print("ℹ️  사용자 이름이 설정되지 않아 프로필 요약을 건너뜁니다.")
            return
        
        # history에서 오디오(User)와 JSON(Model)을 처리하여 대화 복원
        raw_history = getattr(self.ptt.chat, 'history', [])
        conversation_lines = []
        
        for i in range(0, len(raw_history), 2):
            if i + 1 >= len(raw_history): break
            
            model_entry = raw_history[i+1]
            
            user_text = ""
            model_reply = ""
            
            try:
                model_content = model_entry.parts[0].text
                clean_json = re.sub(r"```json\s*", "", model_content)
                clean_json = re.sub(r"```", "", clean_json).strip()
                
                data = json.loads(clean_json)
                user_text = data.get("text", "(음성 인식 불가)") 
                model_reply = data.get("reply", "")
                
            except (json.JSONDecodeError, AttributeError, IndexError):
                continue 

            if user_text and model_reply:
                conversation_lines.append(f"사용자: {user_text}")
                conversation_lines.append(f"모티(AI): {model_reply}")

        if not conversation_lines:
            print("ℹ️  이번 세션에 유효한 대화가 없어 프로필 요약을 건너뜁니다.")
            return

        print(f"⏳ {self.ptt.current_user_name}님의 프로필 요약 업데이트 시도 (종료 작업)...")

        old_summary = self.ptt.initial_chat_summary
        last_seen_str = self.ptt.initial_last_seen_str
        session_conversation_text = "\n".join(conversation_lines)

        try:
            current_time_dt = datetime.now()
            one_week_ago_dt = current_time_dt - timedelta(days=7)
            
            current_time_str = current_time_dt.strftime('%Y년 %m월 %d일 %H시 %M분')
            one_week_ago_str = one_week_ago_dt.strftime('%Y년 %m월 %d일')

            summarizer_prompt = (
                f"당신은 대화 내용을 바탕으로 사용자의 프로필을 관리하는 AI입니다.\n"
                f"현재 시간은 [ {current_time_str} ]입니다.\n"
                f"!! 삭제 기준일은 [ {one_week_ago_str} ]입니다. (오늘로부터 1주일 전)\n"
                f"아래의 [기존 사실] ( {last_seen_str} 기준)을 [이번 세션 전체 대화] ( {current_time_str} 에 종료됨)의 내용으로 업데이트하여, [새로운 사실 목록]을 만드세요.\n\n"
                "규칙:\n"
                "1. 대화에서 '사용자'에 대한 '중요한 개인 정보'(이름, 별명, 생일, 연령, 거주지/지역, 선호 언어, 직업/직무, 직장/학교, 전공, 취미, 좋아하는 음식/요리, 식단 제한/알레르기, 건강 목표, 수면 습관, 대화/응답 스타일, 선호 콘텐츠, 최근 감정, 가족/애완동물 정보, 특별한 날짜, 추억 등)만 추출합니다.\n"
                "2. 단순한 인사나 잡담('안녕', '고마워', '춤 춰')은 무시합니다.\n"
                "3. '새로운 사실 목록'은 항상 간결한 불렛 포인트(-)로 작성합니다.\n"
                "4. [이번 세션 전체 대화]에서 추출할 새 사실이 없다면, [기존 사실]을 (삭제 규칙 적용 후) 그대로 출력합니다.\n"
                "5. [!!기억 삭제 규칙!!] [기존 사실] 목록을 검토하여, [ {one_week_ago_str} ]보다 날짜가 오래된 (즉, 1주일이 지난) 사실은 [새로운 사실 목록]에서 삭제하세요.\n"
                "   - (예시) [기존 사실]에 '- 2025년 11월 1일 강아지 입양'이라고 적혀있고, 삭제 기준일이 '2025년 11월 10일'이라면, 이 사실은 삭제합니다.\n"
                "   - (예외) 단, 사용자의 이름, 생일, MBTI, 가족/반려동물 이름 등 *절대 변하지 않는 핵심 개인정보*는 1주일이 지났더라도 삭제하지 말고 유지해야 합니다.\n"
                "6. [!!날짜/사실 분리 규칙!!] 정보의 유형에 따라 날짜 표기법을 엄격히 구분하세요.\n"
                "   A. [영구적 사실]: 사용자의 이름, 선호도(예: '개구리를 싫어함'), 성격, MBTI, 가족/반려동물 이름 등 *시간과 관계없는 사실*은 날짜를 *절대* 붙이지 마세요.\n"
                "     - (GOOD): '- 거미를 싫어함.'\n"
                "     - (GOOD): '- 사용자 이름은 홍길동입니다.'\n"
                "   B. [특정 시점 일정/사건]: 시험, 약속, 계획, 과거의 특정 사건(예: '어제 병원 감') 등 *특정 날짜에 발생하는 일*은 [사건 발생일]을 맨 앞에 붙여야 합니다.\n"
                "     - (현재 시간: 2025년 11월 18일 / 사용자 발화: '목요일에 컴퓨터 시험 봐.')\n"
                "     - (GOOD): '- 2025년 11월 20일: 컴퓨터 네트워크 시험 예정.'\n"
                "     - (사용자 발화: '오늘 5시에 공부할 거야.')\n"
                "     - (GOOD): '- 2025년 11월 18일 17시: 공부 예정.'\n"
                "   C. [현재 지속 상태]: '지금 시험 기간이다', '오늘 피곤하다'처럼 *대화 시점의 일시적인 상태*는 [대화한 날짜]를 기준으로 기록하세요. ('현재', '오늘' 같은 상대어 금지)\n"
                "     - (현재 시간: 2025년 11월 18일 / 사용자 발화: '나 지금 시험 기간이야.')\n"
                "     - (BAD): '- 현재 시험 기간임.'\n"
                "     - (GOOD): '- 2025년 11월 18일 기준, 시험 기간임.'\n"
                f"[기존 사실 ( {last_seen_str} 기준)]\n{old_summary}\n\n"
                f"[이번 세션 전체 대화 ( {current_time_str} 에 종료됨)]\n"
                f"{session_conversation_text}\n\n"
                "[새로운 사실 목록] (1주일 이내의 정보 + 핵심 정보만 포함)\n"
            )

            summarizer_model = genai.GenerativeModel(self.MODEL_NAME)
            response = summarizer_model.generate_content(summarizer_prompt)
            new_summary = _extract_text(response)

            data = {}
            if os.path.exists(self.ptt.profile_db_file):
                with open(self.ptt.profile_db_file, "r", encoding="utf-8") as f:
                    try: data = json.load(f)
                    except json.JSONDecodeError: data = {}
            
            if not self.ptt.current_user_name in data:
                data[self.ptt.current_user_name] = {}
            
            data[self.ptt.current_user_name]["chat_summary"] = new_summary
            data[self.ptt.current_user_name]["last_seen"] = datetime.now().isoformat()

            with open(self.ptt.profile_db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            
            print(f"✅ {self.ptt.current_user_name}님의 프로필 요약을 (종료 작업으로) 최종 저장했습니다.")

        except Exception as e:
            print(f"❌ (종료 작업) 프로필 요약 업데이트 실패: {e}")