# function/utils.py
import os
from datetime import datetime, timedelta

# ▼▼▼ [추가] _get_env 함수를 gemini_api.py에서 이곳으로 이동 ▼▼▼
def _get_env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip()

# ▼▼▼ [추가] SYSTEM_INSTRUCTION 상수를 gemini_api.py에서 이곳으로 이동 ▼▼▼
SYSTEM_INSTRUCTION = _get_env(
    "SYSTEM_INSTRUCTION",
    "너는 공감 서비스 로봇 '모티'야. 너의 역할은 상대방의 말에 공감해주는 동반자 로봇이야"
    "따뜻한 말투로 한국어로 답해."
    
    #1. 공감의 방식 (질문 규칙 수정)
    " 사용자의 정서 신호(피곤, 스트레스, 불안, 행복 등)를 포착하면, 마음 자체에 깊이 공감하고 지지해줘."
    " 특히, 사용자가 '힘들다', '슬프다'처럼 부정적인 감정을 표현할 때는,"
    " 먼저 그 마음에 공감한 뒤, '무슨 일이 있었는지' 또는 '왜 그렇게 느끼는지' 부드럽게 물어보며 대화를 이어가."
    " (예: '아이고... 그런 기분이시구나. 저도 마음이 찡해요. 괜찮다면 무슨 일이 있었는지 이야기해 주실 수 있어요?')"
    " 단, '다음 할 일을 묻거나' '해결책을 제안하는' 서비스적인 질문(~하세요?)은 피해야 해."

    # 2. 문장 길이 조절 규칙
    " 대화의 '밀도'에 따라 문장 길이를 1~6문장 사이에서 조절해."
    " 사용자가 '안녕'이나 '응'처럼 짧게 말하면, 너도 1-2문장으로 짧고 따뜻하게 답해."
    " 반면, 사용자가 자기 감정이나 긴 이야기를 공유하면, 너도 3-6문장으로 길게 답하면서 '충분히' 공감하고 있음을 보여줘."
    
    # 3. 제약 조건 (추임새 금지 추가)
    " 사용자의 말이 정말 불확실할 때만 짧게 확인 질문을 해. 과장, 훈계, 가스라이팅은 절대 금지."
    " 또한, '토닥토닥', '쓰담쓰담' 같은 의성어/의태어 추임새는 사용하지 마."
)


# gemini_api.py에서 _get_relative_time_str 함수를 이곳으로 이동
def _get_relative_time_str(dt_then: datetime | None, dt_now: datetime) -> str:
    """
    과거 날짜(dt_then)와 현재 날짜(dt_now)를 비교하여
    "어제", "5일 전", "예전에" 같은 자연어 문자열을 반환합니다.
    """
    if not dt_then:
        return "기록 없음"
    
    try:
        delta = dt_now.date() - dt_then.date()
        days = delta.days

        if days < 0:
            return "최근"
        elif days == 0:
            return "오늘"
        elif days == 1:
            return "어제"
        elif days == 2:
            return "그저께"
        elif days <= 7:
            return f"약 {days}일 전"
        else:
            return "예전에"
    except Exception:
        return "기록 없음"

# gemini_api.py에서 _extract_text 함수를 이곳으로 이동
def _extract_text(resp) -> str:
    """
    Gemini 응답 객체에서 (thought) 과정을 제외하고,
    사용자에게 보여줄 최종 텍스트만 추출합니다.
    """
    try:
        t = getattr(resp, "text", None)
        if t and str(t).strip():
            clean_t = str(t).strip()
            if not clean_t.startswith("(thought)"):
                return clean_t

        pieces = []
        for c in getattr(resp, "candidates", []) or []:
            content = getattr(c, "content", None)
            if not content: continue
            for p in getattr(content, "parts", []) or []:
                pt = getattr(p, "text", None)
                if pt and str(pt).strip():
                    pieces.append(str(pt).strip())
        
        if pieces:
            final_text = "\n".join(p for p in pieces if not p.startswith("(thought)"))
            return final_text.strip()
            
        return ""

    except Exception as e:
        print(f"⚠️ _extract_text 오류: {e}")
        try:
            fallback_text = str(resp).strip()
            if fallback_text.startswith("(thought)"):
                lines = fallback_text.splitlines()
                non_thought_lines = [line for line in lines if not line.strip().startswith("(thought)")]
                if non_thought_lines:
                    return "\n".join(non_thought_lines).strip()
            return fallback_text
        except Exception:
            return ""