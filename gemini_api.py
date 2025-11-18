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

from __future__ import annotations

import os
import io
import sys
import json
import base64
import queue
import threading
import wave
import platform
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Callable
import multiprocessing
from functools import wraps

try:
    from dotenv import load_dotenv
    if os.path.exists(".env.local"):
        load_dotenv(dotenv_path=".env.local")
    else:
        load_dotenv()
except Exception:
    pass

import numpy as np
import sounddevice as sd
from pynput import keyboard
import google.generativeai as genai
import requests

IS_WINDOWS = (platform.system() == "Windows")
PROFILE_DB_FILE = "user_profiles.json"

def _get_env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip()

def _find_input_device_by_name(name_substr: str) -> int | None:
    if not name_substr: return None
    key = name_substr.lower()
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get('max_input_channels', 0) > 0 and key in d.get('name', '').lower():
                return i
    except Exception:
        pass
    return None

def _get_relative_time_str(dt_then: datetime | None, dt_now: datetime) -> str:
    """
    [신규 추가]
    과거 날짜(dt_then)와 현재 날짜(dt_now)를 비교하여
    "어제", "5일 전", "예전에" 같은 자연어 문자열을 반환합니다.
    """
    if not dt_then:
        return "기록 없음"
    
    try:
        delta = dt_now.date() - dt_then.date()
        days = delta.days

        if days < 0:
            return "최근" # (미래 시간일 경우, 예외 처리)
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

def keep_awake(func: Callable):
    @wraps(func)
    def wrapper(self: 'PressToTalk', *args, **kwargs):
        stop_keep_alive = threading.Event()
        keep_alive_thread = None

        def keep_alive_worker():
            while not stop_keep_alive.wait(timeout=5.0):
                if self.emotion_queue:
                    self.emotion_queue.put("RESET_SLEEPY_TIMER")

        if self.emotion_queue:
            keep_alive_thread = threading.Thread(target=keep_alive_worker, daemon=True)
            keep_alive_thread.start()

        try:
            return func(self, *args, **kwargs)
        finally:
            if keep_alive_thread:
                stop_keep_alive.set()
            if self.emotion_queue:
                self.emotion_queue.put("RESET_SLEEPY_TIMER")
    return wrapper

SAMPLE_RATE = int(_get_env("SAMPLE_RATE", "16000"))
CHANNELS = int(_get_env("CHANNELS", "1"))
DTYPE = _get_env("DTYPE", "int16")
MODEL_NAME = _get_env("MODEL_NAME", "gemini-2.5-flash")
PROMPT_TEXT = (
    "다음은 사용자의 한국어 음성입니다. 정확한 최종 전사만 출력하세요."
    " 규칙: (1) 사람 발화만, (2) 배경음/중얼거림/비언어음은 삭제,"
    " (3) 종결어미·띄어쓰기·문장부호를 자연스럽게, (4) 기호나 철자가 헷갈리면 의미가 명확한 표현으로,"
    " (5) '춤', '그만' 같은 지시어는 그대로 보존. 오직 텍스트만 출력."
)
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
TTS_RATE = int(_get_env("TTS_RATE", "0"))
TTS_VOLUME = int(_get_env("TTS_VOLUME", "100"))
TTS_FORCE_VOICE_ID = _get_env("TTS_FORCE_VOICE_ID", "")
TTS_OUTPUT_DEVICE = _get_env("TTS_OUTPUT_DEVICE", "")
GREETING_TEXT = _get_env("GREETING_TEXT", "안녕하세요! 모티입니다.")
FAREWELL_TEXT = _get_env("FAREWELL_TEXT", "도움이 되었길 바라요. 언제든 다시 불러주세요.")
ENABLE_GREETING = _get_env("ENABLE_GREETING", "1") not in ("0", "false", "False")

def _extract_text(resp) -> str:
    """
    Gemini 응답 객체에서 (thought) 과정을 제외하고,
    사용자에게 보여줄 최종 텍스트만 추출합니다.
    """
    try:
        # 1. 가장 이상적인 경로: .text 속성에 바로 답변이 있는 경우
        t = getattr(resp, "text", None)
        if t and str(t).strip():
            # (thought)가 포함되어 있는지 확인
            clean_t = str(t).strip()
            if not clean_t.startswith("(thought)"):
                return clean_t

        # 2. 표준 경로: .candidates 리스트에서 parts 순회
        pieces = []
        for c in getattr(resp, "candidates", []) or []:
            content = getattr(c, "content", None)
            if not content: continue
            for p in getattr(content, "parts", []) or []:
                pt = getattr(p, "text", None)
                if pt and str(pt).strip():
                    pieces.append(str(pt).strip())
        
        if pieces:
            # 여러 조각이 있더라도 (thought)로 시작하는 것은 제외하고 합침
            final_text = "\n".join(p for p in pieces if not p.startswith("(thought)"))
            return final_text.strip()
            
        # 3. (thought)만 있고 최종 답변이 없는 경우 (예: 오류)
        #    이 경우, 안전하게 빈 문자열을 반환
        return ""

    except Exception as e:
        print(f"⚠️ _extract_text 오류: {e}")
        try:
            # 4. 최후의 수단 (기존 로직)
            #    (thought)가 포함될 수 있지만, 아예 응답이 없는 것보다 나을 수 있음
            fallback_text = str(resp).strip()
            if fallback_text.startswith("(thought)"):
                # 최후의 수단으로라도 (thought)는 제거 시도
                lines = fallback_text.splitlines()
                non_thought_lines = [line for line in lines if not line.strip().startswith("(thought)")]
                if non_thought_lines:
                    return "\n".join(non_thought_lines).strip()
            return fallback_text # 최악의 경우 (thought)라도 반환
        except Exception:
            return "" # 최종 실패

@dataclass
class RecorderState:
    recording: bool = False
    frames_q: queue.Queue = queue.Queue()
    stream: sd.InputStream | None = None

class SapiTTSWorker:
    def __init__(self):
        self._q: queue.Queue[str | dict | None] = queue.Queue()
        self.voice_id: str | None = None
        self.output_device_desc: str | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=False)
    def start(self):
        self.thread.start()
        self.ready.wait(timeout=5)
    def speak(self, data):
        if not data: return
        text = data if isinstance(data, str) else data.get("text", "")
        print(f"🔊 TTS enqueue ({len(text)} chars)")
        self._q.put(data)
    
    def wait(self):
        """TTS 큐의 모든 작업이 완료될 때까지 기다립니다."""
        self._q.join()

    def close_and_join(self, drain: bool = True, timeout: float = 15.0):
        try:
            if drain:
                print("⏳ TTS 대기: 큐 비우는 중...")
                self._q.join()
            self._q.put(None)
            self.thread.join(timeout=timeout)
        except Exception: pass
    def _run(self):
        pc = None; w32 = None
        try:
            if not IS_WINDOWS:
                print("ℹ️ SAPI는 Windows 전용입니다. (macOS에서는 비활성)"); self.ready.set(); return
            import pythoncom as pc
            import win32com.client as w32
            pc.CoInitialize()
            voice = w32.Dispatch("SAPI.SpVoice")
            voices = voice.GetVoices()
            chosen_voice_id = None
            if TTS_FORCE_VOICE_ID:
                for i in range(voices.Count):
                    v = voices.Item(i)
                    if v.Id == TTS_FORCE_VOICE_ID: chosen_voice_id = v.Id; break
                if not chosen_voice_id: print(f"ℹ️ TTS_FORCE_VOICE_ID를 찾지 못했습니다: {TTS_FORCE_VOICE_ID}")
            if not chosen_voice_id:
                for i in range(voices.Count):
                    v = voices.Item(i)
                    blob = f"{v.Id} {v.GetDescription()}".lower()
                    if any(t in blob for t in ["ko", "korean", "한국어"]): chosen_voice_id = v.Id; break
                if not chosen_voice_id and voices.Count > 0: chosen_voice_id = voices.Item(0).Id
            if chosen_voice_id:
                for i in range(voices.Count):
                    v = voices.Item(i)
                    if v.Id == chosen_voice_id: voice.Voice = v; self.voice_id = v.Id; break
            outs = voice.GetAudioOutputs()
            chosen_out_desc = None
            if TTS_OUTPUT_DEVICE:
                key = TTS_OUTPUT_DEVICE.lower()
                for i in range(outs.Count):
                    o = outs.Item(i); desc = o.GetDescription()
                    if key in desc.lower(): voice.AudioOutput = o; chosen_out_desc = desc; break
                if not chosen_out_desc: print(f"ℹ️ 지정한 출력 장치를 찾지 못했습니다: {TTS_OUTPUT_DEVICE}")
            if not chosen_out_desc and outs.Count > 0:
                try: desc = outs.Item(0).GetDescription()
                except Exception: desc = "System Default"
                chosen_out_desc = desc
            self.output_device_desc = chosen_out_desc
            try: voice.Rate = max(-10, min(10, TTS_RATE))
            except Exception: pass
            try: voice.Volume = max(0, min(100, TTS_VOLUME))
            except Exception: pass

            default_rate = voice.Rate
            default_volume = voice.Volume

            print("🎧 사용 가능한 음성 목록 (SAPI):")
            for i in range(voices.Count): v = voices.Item(i); print(f"  - [{i}] id='{v.Id}', desc='{v.GetDescription()}'")
            print("🔉 사용 가능한 출력 장치 (SAPI):")
            for i in range(outs.Count): o = outs.Item(i); print(f"  - [{i}] '{o.GetDescription()}'")
            print(f"▶ 선택된 음성 id='{self.voice_id}'")
            print(f"▶ 선택된 출력='{self.output_device_desc}'")
            self.ready.set()
            voice.Speak("T T S가 준비되었습니다.")
            while True:
                item = self._q.get()
                if item is None: self._q.task_done(); break
                try:
                    if isinstance(item, dict):
                        text = item.get("text")
                        voice.Rate = item.get("rate", default_rate)
                        voice.Volume = item.get("volume", default_volume)
                    else:
                        text = item

                    if text:
                        print("🔈 TTS speaking..."); 
                        voice.Speak(text, 1); 
                        print("✅ TTS done")

                finally:
                    voice.Rate = default_rate
                    voice.Volume = default_volume
                    self._q.task_done()
        except Exception as e: print(f"ℹ️ TTS 스레드 오류: {e}"); self.ready.set()
        finally:
            try:
                if pc is not None: pc.CoUninitialize()
            except Exception: pass

class TypecastTTSWorker:
    def __init__(self):
        self._q: queue.Queue[str | dict | None] = queue.Queue()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=False)
    def start(self):
        self.thread.start(); self.ready.wait(timeout=5)
    def speak(self, data):
        if not data: return
        text = data if isinstance(data, str) else data.get("text", "")
        print(f"🔊 TTS enqueue ({len(text)} chars)")
        self._q.put(data)

    def wait(self):
        """TTS 큐의 모든 작업이 완료될 때까지 기다립니다."""
        self._q.join()

    def close_and_join(self, drain: bool = True, timeout: float = 30.0):
        try:
            if drain: self._q.join()
            self._q.put(None); self.thread.join(timeout=timeout)
        except Exception: pass
    def _run(self):
        try:
            api_key = _get_env("TYPECAST_API_KEY")
            voice_id = _get_env("TYPECAST_VOICE_ID")
            if not api_key or not voice_id:
                print("❗ TYPECAST_API_KEY 또는 TYPECAST_VOICE_ID가 비어있습니다."); self.ready.set(); return
            model = _get_env("TYPECAST_MODEL", "ssfm-v21")
            language = _get_env("TYPECAST_LANGUAGE", "kor")
            audio_format = _get_env("TYPECAST_AUDIO_FORMAT", "wav")
            emotion = _get_env("TYPECAST_EMOTION", "")
            intensity = float(_get_env("TYPECAST_EMOTION_INTENSITY", "1.0") or "1.0")
            seed_env = _get_env("TYPECAST_SEED", "")
            seed = int(seed_env) if (seed_env and seed_env.isdigit()) else None
            self.ready.set()
            print("▶ Typecast TTS 준비 완료")
            url = "https://api.typecast.ai/v1/text-to-speech"
            headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
            while True:
                item = self._q.get()
                if item is None: self._q.task_done(); break
                try:
                    if isinstance(item, dict):
                        text = item.get("text")
                        rate_sapi = item.get("rate", 0) 
                        rate_multiplier = 1.0 + (rate_sapi / 10.0) * 0.5 
                        volume = item.get("volume", 100)
                        pitch = item.get("pitch", 0)
                    else:
                        text = item
                        rate_multiplier = 1.0
                        volume = 100
                        pitch = 0

                    if not text: continue
                    
                    payload = {
                        "voice_id": voice_id, "text": text, "model": model, "language": language, 
                        "output": {
                            "volume": volume, 
                            "audio_pitch": pitch, 
                            "audio_tempo": rate_multiplier, 
                            "audio_format": audio_format
                        }
                    }
                    if emotion: payload["prompt"] = {"emotion_preset": emotion, "emotion_intensity": intensity}
                    if seed is not None: payload["seed"] = seed
                    r = requests.post(url, headers=headers, json=payload, timeout=60)
                    if r.status_code == 200:
                        data = r.content
                        with io.BytesIO(data) as buf:
                            with wave.open(buf, "rb") as wf:
                                sr = wf.getframerate(); sampwidth = wf.getsampwidth(); frames = wf.readframes(wf.getnframes())
                        if sampwidth == 2: audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                        else: audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                        sd.play(audio, sr); sd.wait(); print("✅ TTS done")
                    else: print(f"❌ Typecast 오류 {r.status_code}: {r.text[:200]}")
                finally: self._q.task_done()
        except Exception as e: print(f"ℹ️ Typecast TTS 스레드 오류: {e}"); self.ready.set()

class PressToTalk:
    def __init__(self,
                 start_dance_cb: Optional[Callable[[], None]] = None,
                 stop_dance_cb: Optional[Callable[[], None]] = None,
                 play_rps_motion_cb: Optional[Callable[[], None]] = None,
                 play_greeting_cb: Optional[Callable[[], None]] = None,
                 play_both_arms_cb: Optional[Callable[[], None]] = None,
                 play_right_arm_cb: Optional[Callable[[], None]] = None,
                 play_left_arm_cb: Optional[Callable[[], None]] = None,
                 play_wheel_wiggle_cb: Optional[Callable[[], None]] = None,
                 emotion_queue: Optional[queue.Queue] = None,
                 subtitle_queue: Optional[multiprocessing.Queue] = None, 
                 hotword_queue: Optional[queue.Queue] = None,
                 stop_event: Optional[threading.Event] = None,
                 rps_command_q: Optional[multiprocessing.Queue] = None,
                 rps_result_q: Optional[multiprocessing.Queue] = None,
                 sleepy_event: Optional[threading.Event] = None,
                 shared_state: Optional[dict] = None,
                 ox_command_q: Optional[multiprocessing.Queue] = None,
                 ox_result_q: Optional[multiprocessing.Queue] = None,
                 mouth_event_queue: Optional[queue.Queue] = None,
                 # ▼▼▼ [수정] 끄덕임 콜백 함수를 괄호 안으로 이동시킴 ▼▼▼
                 perform_head_nod_cb: Optional[Callable[[int], None]] = None
                 ): 
        
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key or not api_key.strip():
            print("❗ GOOGLE_API_KEY가 없습니다."); sys.exit(1)

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(MODEL_NAME)
        self.chat = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_INSTRUCTION).start_chat(history=[])

        self.router_model = genai.GenerativeModel(
            MODEL_NAME,
            system_instruction=(
                "너는 명령 라우터다. 한국어 문장을 보고 의도를 분류한다. "
                "introduction=사용자가 자신의 이름을 명확히 밝히는 문장. (예: '내 이름은 OOO이야', '저는 OOO입니다', 'OOO이라고 해'). "
                "dance=사용자가 실제로 춤을 '시작하라고' 명령/요청/승인. "
                "game=가위바위보 게임을 시작하자는 요청. "
                "ox_quiz=얼굴 인식 OX 퀴즈 게임을 시작하자는 요청. "
                "joke=개그나 농담을 해달라는 명확한 요청. "
                "stop=춤을 '멈추라'는 명령/요청/승인. "
                "chat=일반 대화(질문/잡담/설명/감정표현/춤에 대한 견해·가정적 질문 포함). "
                "부정/금지/거절 표현(예:'춤 추지 마','춤은 안돼','그만두지 말고 계속')은 정확히 반영하라. "
                "오직 아래 JSON만 출력:\n"
                '{ "intent": "dance|stop|game|ox_quiz|chat|joke|introduction", "normalized_text": "<의미만 보존한 간결한 문장>", '
                '"speakable_reply": "<의도가 chat일 때 1~2문장 공감형 짧은 답변. dance/stop/game/joke/ox_quiz이면 빈 문자열>" }'
                '"name": "<intent가 introduction일 때만 문맥을 파악해 추출한 [사람 이름]. 아닐 경우 null>" }'
            ),
            generation_config={"response_mime_type": "application/json", "temperature": 0.2}
        )

        self.current_user_name = None
        self.profile_db_file = PROFILE_DB_FILE
        self._init_profile_db()
        self.initial_chat_summary = "아직 기록된 내용이 없습니다."
        self.initial_last_seen_str = "기록 없음"
        self.start_dance_cb = start_dance_cb
        self.stop_dance_cb  = stop_dance_cb
        self.play_rps_motion_cb = play_rps_motion_cb
        self.play_greeting_cb = play_greeting_cb
        self.play_both_arms_cb = play_both_arms_cb
        self.play_right_arm_cb = play_right_arm_cb
        self.play_left_arm_cb = play_left_arm_cb
        self.play_wheel_wiggle_cb = play_wheel_wiggle_cb
        self.emotion_queue = emotion_queue
        self.subtitle_queue = subtitle_queue
        self.hotword_queue = hotword_queue
        self.stop_event = stop_event or threading.Event()
        
        self.mouth_event_queue = mouth_event_queue
        self.listening_enabled = threading.Event() 
        self.mouth_listener_thread = None 
        
        self.last_activity_time = 0
        self.current_listener = None

        self.rps_command_q = rps_command_q
        self.rps_result_q  = rps_result_q
        self.ox_command_q = ox_command_q
        self.ox_result_q = ox_result_q
        self.busy_lock = threading.Lock()
        self.busy_signals = 0
        self.background_keep_alive_thread = None
        self.stop_background_keep_alive = threading.Event()

        self.perform_head_nod_cb = perform_head_nod_cb
        self.nodding_thread = None
        self.stop_nodding_event = threading.Event()

        default_engine = "sapi" if IS_WINDOWS else "typecast"
        engine = _get_env("TTS_ENGINE", default_engine).lower()
        if engine == "sapi" and not IS_WINDOWS: engine = "typecast"
        if engine == "typecast": self.tts = TypecastTTSWorker()
        else: self.tts = SapiTTSWorker()
        self.tts.start()

        self.state = RecorderState()
        self._print_intro()
        if ENABLE_GREETING:
            self._speak_and_subtitle(GREETING_TEXT)
            if self.emotion_queue: self.emotion_queue.put("NEUTRAL")

        self.sleepy_event = sleepy_event
        self.shared_state = shared_state

        if self.sleepy_event:
            self.snoring_thread = threading.Thread(target=self._snoring_worker, daemon=True)
            self.snoring_thread.start()

        self.announcement_thread = None
        self.stop_announcement_event = threading.Event()
        self.announcement_active = False

    def _init_profile_db(self):
        """JSON 프로필 DB 파일이 없으면 빈 객체로 생성합니다."""
        if not os.path.exists(self.profile_db_file):
            print(f"ℹ️ 프로필 DB 파일({self.profile_db_file})이 없어 새로 생성합니다.")
            try:
                with open(self.profile_db_file, "w", encoding="utf-8") as f:
                    json.dump({}, f)
            except Exception as e:
                print(f"❌ 프로필 DB 파일 생성 실패: {e}")

    def load_profile(self, name: str):
        """사용자 이름을 기반으로 '요약된 사실'을 로드하여 시스템 프롬프트에 주입합니다."""
        print(f"⏳ {name}님의 프로필 로드를 시도합니다...")
        
        chat_summary = "아직 기록된 내용이 없습니다."
        last_seen_str = "기록 없음" # (요약기록용 절대 날짜)
        relative_time_str = "기록 없음" # (채팅 프롬프트용 상대 날짜)

        try:
            if os.path.exists(self.profile_db_file):
                with open(self.profile_db_file, "r", encoding="utf-8") as f:
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
                        
                        # 상대 시간 계산 ▼▼▼
                        relative_time_str = _get_relative_time_str(last_seen_dt_obj, dt_now) 
                        
                        # 기존 'last_seen_str'는 종료 시 요약기를 위해 절대 날짜 형식 유지 ▼▼▼
                        last_seen_str = last_seen_dt_obj.strftime('%Y년 %m월 %d일 %H시 %M분')
                    except ValueError:
                        pass # 파싱 실패 시 "기록 없음" 유지
                
                # 로그에도 자연스러운 상대 시간 사용 ▼▼▼
                print(f"✅ {name}님의 프로필을 성공적으로 로드했습니다. (마지막 대화: {relative_time_str})")
                self._speak_and_subtitle(f"{name}님, 다시 만나서 반가워요!")
            
            else:
                print(f"ℹ️ {name}님의 프로필이 없습니다. 새로 생성합니다.")
                self._speak_and_subtitle(f"{name}님, 만나서 반가워요! 오늘부터 기억해둘게요.")
        
        except Exception as e:
            print(f"❌ 프로필 로드 실패: {e}")
            self._speak_and_subtitle(f"{name}님, 만나서 반가워요!")

        self.current_user_name = name
        self.initial_chat_summary = chat_summary
        self.initial_last_seen_str = last_seen_str 
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
            "3. [중요 기억]의 내용을 대화에 적극적으로 활용하여, 당신이 사용자를 기억하고 있음을 보여주세요.\n"
            "4. [!! 중요 대화 규칙 !!] 기억 속의 사실을 언급할 때, '2025년 11월 17일'처럼 [절대 날짜]를 직접 말하지 마세요.\n"
            "   - 대신, [현재 시간]을 기준으로 '어제', '며칠 전에', '예전에' 같은 [상대 시간]으로 자연스럽게 표현하세요.\n"
            "   - (예: [중요 기억]에 '- 2025년 11월 17일: 개구리를 싫어함'이라고 적혀있고 오늘이 11월 18일이라면, '아, 맞다. 어제 개구리 싫어한다고 하셨죠!'라고 말하세요.)\n"
            "--- 중요 기억 끝 ---"
        )
        
        self.chat = genai.GenerativeModel(
            MODEL_NAME, 
            system_instruction=enhanced_system_instruction
        ).start_chat(history=[])

    def update_profile_summary_at_exit(self):
        """
        프로그램 종료 시, self.chat.history에 누적된 전체 대화 내역을 바탕으로
        프로필 요약을 *한 번만* 업데이트하고 저장합니다.
        """
        if not self.current_user_name:
            print("ℹ️  사용자 이름이 설정되지 않아 프로필 요약을 건너뜁니다.")
            return
        
        # self.chat.history에는 'chat' 인텐트의 대화만 쌓입니다.
        chat_history_entries = [
            entry for entry in getattr(self.chat, 'history', []) 
            if entry.role in ('user', 'model') and entry.parts and getattr(entry.parts[0], 'text', None)
        ]

        if not chat_history_entries:
            print("ℹ️  이번 세션에 'chat' 대화가 없어 프로필 요약을 건너뜁니다.")
            return

        print(f"⏳ {self.current_user_name}님의 프로필 요약 업데이트 시도 (종료 작업)...")

        # 1. 기존 요약 정보 가져오기 (load_profile에서 저장해둔 값)
        old_summary = self.initial_chat_summary
        last_seen_str = self.initial_last_seen_str

        # 2. 이번 세션의 전체 대화 내역 포매팅하기
        conversation_lines = []
        for entry in chat_history_entries:
            role = "사용자" if entry.role == "user" else "모티(AI)"
            text = entry.parts[0].text
            conversation_lines.append(f"{role}: {text}")
        
        session_conversation_text = "\n".join(conversation_lines)

        # 3. Summarizer 프롬프트 생성 (기존 로직 활용, '방금 나눈 대화' -> '이번 세션 전체 대화')
        try:
            current_time_dt = datetime.now()
            one_week_ago_dt = current_time_dt - timedelta(days=7) # 1주일 전 날짜 계산
            
            current_time_str = current_time_dt.strftime('%Y년 %m월 %d일 %H시 %M분')
            one_week_ago_str = one_week_ago_dt.strftime('%Y년 %m월 %d일') # 1주일 전 날짜 (삭제 기준)

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
                "      - (GOOD): '- 개구리를 싫어함.'\n"
                "      - (GOOD): '- 사용자 이름은 강은성입니다.'\n"
                "   B. [특정 시점 일정/사건]: 시험, 약속, 계획, 과거의 특정 사건(예: '어제 병원 감') 등 *특정 날짜에 발생하는 일*은 [사건 발생일]을 맨 앞에 붙여야 합니다.\n"
                "      - (현재 시간: 2025년 11월 18일 / 사용자 발화: '목요일에 컴퓨터 시험 봐.')\n"
                "      - (GOOD): '- 2025년 11월 20일: 컴퓨터 네트워크 시험 예정.'\n"
                "      - (사용자 발화: '오늘 5시에 공부할 거야.')\n"
                "      - (GOOD): '- 2025년 11월 18일 17시: 공부 예정.'\n"
                "   C. [현재 지속 상태]: '지금 시험 기간이다', '오늘 피곤하다'처럼 *대화 시점의 일시적인 상태*는 [대화한 날짜]를 기준으로 기록하세요. ('현재', '오늘' 같은 상대어 금지)\n"
                "      - (현재 시간: 2025년 11월 18일 / 사용자 발화: '나 지금 시험 기간이야.')\n"
                "      - (BAD): '- 현재 시험 기간임.'\n"
                "      - (GOOD): '- 2025년 11월 18일 기준, 시험 기간임.'\n"
                f"[기존 사실 ( {last_seen_str} 기준)]\n{old_summary}\n\n"
                f"[이번 세션 전체 대화 ( {current_time_str} 에 종료됨)]\n"
                f"{session_conversation_text}\n\n"
                "[새로운 사실 목록] (1주일 이내의 정보 + 핵심 정보만 포함)\n"
            )

            summarizer_model = genai.GenerativeModel(MODEL_NAME)
            response = summarizer_model.generate_content(summarizer_prompt)
            new_summary = _extract_text(response)

            # 4. JSON 파일 열고 저장하기 (기존 로직과 유사)
            data = {}
            if os.path.exists(self.profile_db_file):
                with open(self.profile_db_file, "r", encoding="utf-8") as f:
                    try: data = json.load(f)
                    except json.JSONDecodeError: data = {}
            
            if not self.current_user_name in data:
                data[self.current_user_name] = {}
            
            data[self.current_user_name]["chat_summary"] = new_summary
            data[self.current_user_name]["last_seen"] = datetime.now().isoformat()

            with open(self.profile_db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            
            print(f"✅ {self.current_user_name}님의 프로필 요약을 (종료 작업으로) 최종 저장했습니다.")

            # [수정] 프로그램 종료 시점이므로, self.chat을 재-초기화하는 로직은 제거됨.

        except Exception as e:
            print(f"❌ (종료 작업) 프로필 요약 업데이트 실패: {e}")

    def _listening_nod_worker(self):
        """사용자가 말하는 동안 랜덤하게 고개를 끄덕이는 백그라운드 스레드"""
        print("👂 경청 모드: 랜덤 끄덕임 스레드 시작...")
        
        # 스레드가 시작하고 바로 끄덕이지 않도록 초반에 랜덤 대기 (0.5초 ~ 1.5초)
        start_wait = random.uniform(0.5, 1.5)
        # wait() 함수는 1) 대기하거나 2) stop_nodding_event 신호를 받으면 즉시 True를 반환합니다.
        interrupted = self.stop_nodding_event.wait(timeout=start_wait)
        if interrupted: # 대기 중에 중지 신호가 오면 바로 종료
            print("👂 경청 모드: 시작 전 중지됨.")
            return

        while not self.stop_nodding_event.is_set():
            # 끄덕임 횟수를 랜덤으로 결정합니다.
            if random.random() < 0.3: # 20% 확률로
                reps = 2 # 빠르게 두 번 끄덕이기
                print("👂 (경청) 끄덕임 x2")
            else: # 80% 확률로
                reps = 1 # 한 번 끄덕이기
                print("👂 (경청) 끄덕임 x1")

            if callable(self.perform_head_nod_cb):
                try:
                    # 결정된 횟수(reps)만큼 끄덕임 스레드 시작
                    threading.Thread(target=self.perform_head_nod_cb, args=(reps,), daemon=True).start()
                except Exception as e:
                    print(f"⚠️ 경청 끄덕임 중 오류: {e}")
            
            # 다음 끄덕임까지 랜덤 대기 (1.5초 ~ 4.0초)
            wait_time = random.uniform(1.5, 4.0)
            
            interrupted = self.stop_nodding_event.wait(timeout=wait_time)
            
            if interrupted:
                break # 녹음이 중지되었으므로 루프 탈출
        
        print("👂 경청 모드: 랜덤 끄덕임 스레드 종료.")

    def _mouth_listener_worker(self):
        """A dedicated thread to listen for mouth events."""
        print("▶ 🔊 Mouth-to-Talk listener thread started.")
        while not self.stop_event.is_set():
            try:
                msg = self.mouth_event_queue.get(timeout=0.2) 
                
                if msg == "START_RECORDING":
                    if self.listening_enabled.is_set():
                        if self.busy_signals > 0:
                            print(f"👄 게임 중 말 인식 멈춤 (busy_signals: {self.busy_signals})")
                            continue
                        self._start_recording()
                elif msg == "STOP_RECORDING":
                    self._stop_recording_and_transcribe()

            except queue.Empty:
                continue 
            except Exception as e:
                print(f"❌ Mouth listener error: {e}")
        print("■ 🔊 Mouth-to-Talk listener thread stopped.")

    def _speak_and_subtitle(self, text_data: str | dict):
        """
        TTS 출력과 자막을 문장 단위로 동기화하여 처리합니다.
        - 긴 문자열은 문장(.!?)으로 분리하여 하나씩 순차적으로 말하고 자막을 표시합니다.
        - '가위! 바위!'와 '보!' 같이 연속 호출되어도 음성 출력을 기다립니다.
        - 속도/볼륨 조절을 위한 dict 입력은 기존처럼 바로 처리합니다.
        """
        import re

        if not text_data:
            return
        
        self.raise_busy_signal()
        try:
            if isinstance(text_data, dict):
                text_to_display = text_data.get("text", "")
                if self.subtitle_queue and text_to_display:
                    self.subtitle_queue.put(text_to_display)
                self.tts.speak(text_data)
                return
            
            text_to_process = str(text_data)
            sentences = re.split(r'(?<=[.!?])\s+', text_to_process)
            sentences = [s.strip() for s in sentences if s.strip()]

            if not sentences:
                return

            for sentence in sentences:
                if self.subtitle_queue:
                    self.subtitle_queue.put(sentence)
                
                self.tts.speak(sentence)
                self.tts.wait()
        finally:
            self.lower_busy_signal()
        
    def _announcement_worker(self):
        announcement_text = "한동의 미남 미녀 여러분 안녕하세요. 잠시만 주목해주세요! 8시부터 모티와 함께하는 즐거운 시간이 시작됩니다. 많은 관심과 참여 부탁드려요. "
        print("📢 안내 방송 스레드가 시작되었습니다.")
        try:
            while not self.stop_announcement_event.is_set():
                self._speak_and_subtitle(announcement_text)
                interrupted = self.stop_announcement_event.wait(timeout=60.0)
                if interrupted:
                    break
        finally:
            self.lower_busy_signal()
            self.announcement_active = False
            print("🛑 안내 방송 스레드가 종료되었습니다.")

    def toggle_announcement(self):
        """'p' 키에 반응해 안내 방송을 켜고 끄는 더 안정적인 함수입니다."""
        is_running = self.announcement_thread is not None and self.announcement_thread.is_alive()

        if is_running:
            print("...안내 방송 중지 신호를 보냅니다...")
            self.stop_announcement_event.set()
        else:
            print("...안내 방송 시작을 시도합니다...")
            self.raise_busy_signal()
            self.announcement_active = True
            self.stop_announcement_event.clear()
            self.announcement_thread = threading.Thread(target=self._announcement_worker, daemon=True)
            self.announcement_thread.start()
            print("✅ 60초마다 안내 방송을 시작합니다.")

    def _fetch_quizzes_in_background(self, result_container: list):
            """[백그라운드 스레드용] Gemini API를 호출하여 퀴즈 목록을 생성하고 result_container에 저장합니다."""
            print("  - 🏃 (백그라운드) 본 게임 퀴즈 생성을 시작합니다...")
            try:
                quiz_prompt = (
                    "어린이도 이해할 수 있는, 재미있고 간단한 상식 OX 퀴즈를 한국어로 10개만 만들어줘. "
                    "이전에 출제했던 문제와는 다른 새로운 주제로 내줘."
                    "출력은 반드시 다음 JSON 리스트 형식이어야 해. 다른 설명은 절대 추가하지 마.\n"
                    '[{"question": "<퀴즈1 질문>", "answer": "O 또는 X"}, {"question": "<퀴즈2 질문>", "answer": "O 또는 X"}]'
                )
                quiz_response = genai.GenerativeModel(MODEL_NAME).generate_content(
                    quiz_prompt, 
                    generation_config={"response_mime_type": "application/json"}
                )
                raw_json = _extract_text(quiz_response)
                quizzes = json.loads(raw_json)
                result_container.extend(quizzes)
                print(f"  - ✅ (백그라운드) 퀴즈 {len(quizzes)}개 생성 완료!")
            except Exception as e:
                print(f"  - ❌ (백그라운드) 퀴즈 생성 실패: {e}")  

    def _print_intro(self):
        print("\n=== Gemini PTT (통합 버전) ===")
        print("▶ '안녕 모티'로 호출(SLEEPY 상태) → 스페이스바로 대화(NEUTRAL 상태) → ESC로 종료")
        print("▶ [User ] 전사 결과 / [Gemini] 모델 답변")
        print("▶ 키워드: '춤' → 댄스 시작 / '그만' → 댄스 정지 / '가위바위보' → 게임 시작 / 'OX 게임")
        print(f"▶ MODEL={MODEL_NAME}, SR={SAMPLE_RATE}Hz")
        v_id, out_desc = getattr(self.tts, "voice_id", None), getattr(self.tts, "output_device_desc", None)
        if v_id: print(f"▶ TTS Voice : {v_id}")
        if out_desc: print(f"▶ TTS Output: {out_desc}")
        print("----------------------------------------------------------------\n")

    def raise_busy_signal(self):
        """백그라운드 작업 시작을 알리고, 필요하면 keep-alive 스레드를 활성화합니다."""
        with self.busy_lock:
            self.busy_signals += 1
            print(f"⚡ 바쁨 신호 증가 (현재: {self.busy_signals})")
            if self.busy_signals == 1 and self.emotion_queue:
                self.stop_background_keep_alive.clear()
                
                def worker():
                    while not self.stop_background_keep_alive.wait(5.0):
                        if self.emotion_queue:
                            self.emotion_queue.put("RESET_SLEEPY_TIMER")
                    print("☕ 백그라운드 keep-alive 자연 종료")

                self.background_keep_alive_thread = threading.Thread(target=worker, daemon=True)
                self.background_keep_alive_thread.start()
                print("🏃 백그라운드 keep-alive 시작됨")

    def lower_busy_signal(self):
        """백그라운드 작업 종료를 알리고, 모든 작업이 끝나면 keep-alive 스레드를 중지합니다."""
        with self.busy_lock:
            self.busy_signals = max(0, self.busy_signals - 1)
            print(f"⚡ 바쁨 신호 감소 (현재: {self.busy_signals})")
            if self.busy_signals == 0:
                self.stop_background_keep_alive.set()
                self.background_keep_alive_thread = None
                self.last_activity_time = time.time()
                print("✅ 모든 백그라운드 작업 완료. keep-alive 중지됨")
                print("✅ RESET_SLEEPY_TIMER")

    def _audio_callback(self, indata, frames, time_info, status):
        if status: print(f"[오디오 경고] {status}", file=sys.stderr)
        self.state.frames_q.put(indata.copy())

    def _start_recording(self):
        if self.state.recording: return
        if self.emotion_queue:
            self.emotion_queue.put("RESET_SLEEPY_TIMER")
            self.emotion_queue.put("LISTENING")

        self.last_activity_time = time.time()
        print("✅ User started speaking. Activity timer reset.")

        while not self.state.frames_q.empty():
            try: self.state.frames_q.get_nowait()
            except queue.Empty: break
        device_idx = None
        env_dev = os.environ.get("INPUT_DEVICE_INDEX")
        if env_dev and env_dev.strip():
            try: device_idx = int(env_dev.strip())
            except Exception: device_idx = None
        if device_idx is None:
            env_name = os.environ.get("INPUT_DEVICE_NAME", "")
            if env_name: device_idx = _find_input_device_by_name(env_name)
        try:
            if device_idx is not None: dinfo = sd.query_devices(device_idx, 'input')
            else: default_in = sd.default.device[0]; dinfo = sd.query_devices(default_in, 'input')
            print(f"🎚️  입력 장치: {dinfo['name']}")
        except Exception: pass
        self.state.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE, callback=self._audio_callback, blocksize=0, device=device_idx)
        self.state.stream.start()
        self.state.recording = True
        print("🎙️  녹음 시작...")
        
        if callable(self.perform_head_nod_cb) and (self.nodding_thread is None or not self.nodding_thread.is_alive()):
            self.stop_nodding_event.clear()
            self.nodding_thread = threading.Thread(target=self._listening_nod_worker, daemon=True)
            self.nodding_thread.start()

    def _stop_recording_and_transcribe(self):
        if not self.state.recording: return
        if self.emotion_queue:
            self.emotion_queue.put("THINKING")
        self.last_activity_time = time.time()
        print("✅ User stopped speaking. Activity timer reset.")
        print("⏹️  녹음 종료, 전사 중...")
        self.state.recording = False
        try:
            if self.state.stream: self.state.stream.stop(); self.state.stream.close()
        finally: self.state.stream = None
        
        self.stop_nodding_event.set()
        
        chunks = []
        while not self.state.frames_q.empty(): chunks.append(self.state.frames_q.get())
        if not chunks: print("(녹음 데이터가 없습니다.)\n"); return
        audio_np = np.concatenate(chunks, axis=0)
        wav_bytes = self._to_wav_bytes(audio_np, SAMPLE_RATE, CHANNELS, DTYPE)
        threading.Thread(target=self._transcribe_then_chat, args=(wav_bytes,), daemon=True).start()

    @staticmethod
    def _to_wav_bytes(audio_np: np.ndarray, samplerate: int, channels: int, dtype: str) -> bytes:
        with io.BytesIO() as buf:
            with wave.open(buf, 'wb') as wf:
                wf.setnchannels(channels); wf.setsampwidth(np.dtype(dtype).itemsize)
                wf.setframerate(samplerate); wf.writeframes(audio_np.tobytes())
            return buf.getvalue()

    def _route_intent(self, text: str) -> dict:
        try:
            resp = self.router_model.generate_content(text)
            raw = _extract_text(resp); data = json.loads(raw)
            if not isinstance(data, dict): raise ValueError("router JSON is not a dict")
            intent = data.get("intent", "chat")
            if intent not in ("dance", "stop", "game", "chat", "joke", "ox_quiz", "introduction"): intent = "chat"
            return {"intent": intent, "normalized_text": str(data.get("normalized_text", text)), "speakable_reply": str(data.get("speakable_reply", "")) if intent == "chat" else "", "name": data.get("name")}
        except Exception as e:
            print(f"(router 폴백) {e}")
            low = text.lower()
            if any(neg in text for neg in ["하지 마", "하지마", "안돼", "안 돼", "그만두지 마", "멈추지 마"]): return {"intent": "chat", "normalized_text": text, "speakable_reply": ""}
            if "그만" in text: return {"intent": "stop", "normalized_text": text, "speakable_reply": ""}
            if "춤" in text: return {"intent": "dance", "normalized_text": text, "speakable_reply": ""}
            if any(w in low for w in ["농담", "개그"]): return {"intent": "joke", "normalized_text": text, "speakable_reply": ""}
            if "ox 퀴즈" in low or "ox게임" in low or "ox 게임" in low: return {"intent": "ox_quiz", "normalized_text": text, "speakable_reply": ""}
            if any(w in low for w in ["가위바위보", "게임"]): return {"intent": "game", "normalized_text": text, "speakable_reply": ""}
            return {"intent": "chat", "normalized_text": text, "speakable_reply": ""}
    
    def _analyze_and_send_emotion(self, text: str):
        if not self.emotion_queue or not text: return
        low_text = text.lower()
        if any(w in low_text for w in ["신나", "재밌", "좋아", "행복", "최고"]): self.emotion_queue.put("HAPPY")
        elif any(w in low_text for w in ["놀라운", "놀랐", "깜짝", "세상에"]): self.emotion_queue.put("SURPRISED")
        elif any(w in low_text for w in ["슬퍼", "우울", "힘들", "속상"]): self.emotion_queue.put("SAD")
        elif any(w in low_text for w in ["화나", "짜증", "싫어", "최악"]): self.emotion_queue.put("ANGRY")
        elif any(w in low_text for w in ["사랑", "다정", "따뜻", "고마워"]): self.emotion_queue.put("TENDER")
        elif any(w in low_text for w in ["궁금", "생각", "글쎄", "흠.."]): self.emotion_queue.put("THINKING")
        else: self.emotion_queue.put("NEUTRAL")

    @keep_awake
    def _transcribe_then_chat(self, wav_bytes: bytes):
        intent = None
        user_text = ""
        model_text = ""
        is_chat_intent = False
        self.raise_busy_signal()
        
        try:
            b64 = base64.b64encode(wav_bytes).decode("ascii")
            parts = [{"text": PROMPT_TEXT}, {"inline_data": {"mime_type": "audio/wav", "data": b64}}]
            resp = self.model.generate_content(parts)
            user_text = _extract_text(resp)
            if not user_text: 
                print("📝 전사 결과가 비어 있습니다.\n")

                if self.emotion_queue:
                    self.emotion_queue.put("NEUTRAL")

                self.lower_busy_signal()
                return
            
            ts = datetime.now().strftime("%H:%M:%S"); print(f"[{ts}] [User ] {user_text}")

            route = self._route_intent(user_text)
            intent = route.get("intent", "chat")

            if self.emotion_queue and intent not in ("dance", "game", "ox_quiz"):
                self.emotion_queue.put("NEUTRAL") 
                print("😊 THINKING 종료: NEUTRAL로 즉시 전환")

            if intent == "chat":
                is_chat_intent = True

            elif intent == "introduction":
                name = route.get("name")
                if name:
                    print(f"💡 의도: INTRODUCTION, AI가 추출한 이름: {name}")
                    self.load_profile(name)
                else:
                    print("⚠️  의도: INTRODUCTION (이름 추출 실패). Chat으로 폴백.")
                    intent = "chat"
                    is_chat_intent = True

            if not is_chat_intent: # 폴백되어 chat으로 가지 않은, 순수 introduction일 때
                    if self.emotion_queue:
                        print("... Introduction 완료, NEUTRAL로 표정 리셋 ...")
                        self.emotion_queue.put("NEUTRAL")

            model_text, speak_text = "", ""

            if intent == "chat":
                reply = self.chat.send_message(user_text); model_text = _extract_text(reply) or ""
                speak_text = model_text
                self._analyze_and_send_emotion(model_text) 

                print(f"[{ts}] [Gemini] {model_text}\n")

                if speak_text:
                    self._speak_and_subtitle(speak_text)

                if self.emotion_queue:
                    print("... TTS 완료, NEUTRAL로 표정 리셋 ...")
                    self.emotion_queue.put("NEUTRAL")

                speak_text = ""

            elif intent == "dance":
                print("💡 의도: DANCE START")
                if callable(self.start_dance_cb):
                    try: 
                        self.start_dance_cb()
                    except Exception as e: print(f"⚠️ start_dance_cb 실행 오류: {e}")
                
                if self.emotion_queue:
                    if self.emotion_queue: self.emotion_queue.put("EXCITED")
                    print(f"💃 춤 시작! 표정을 EXCITED로 변경합니다.")

                model_text = "네! 모티가 춤을 춰볼게요"; speak_text = "네! 모티가 춤을 춰볼게요"

            elif intent == "stop":
                print("💡 의도: DANCE STOP")
                if callable(self.stop_dance_cb):
                    try: 
                        self.stop_dance_cb()
                    except Exception as e: print(f"⚠️ stop_dance_cb 실행 오류: {e}")
                
                if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                model_text = "(춤 정지 명령 처리)"

            elif intent == "joke":
                print("💡 의도: JOKE (AI 실시간 생성 방식)")
                try:
                    if self.emotion_queue: self.emotion_queue.put("THINKING")
                    self._speak_and_subtitle("위잉 회로 풀가동! 여러분의 모터가 빠질만한 개그를 생성하는 중입니다")
                    self.tts.wait()
                    joke_prompt = (
                        "너는 '모티'라는 로봇이야. '로봇', '컴퓨터', '전기'와 관련된, 어린아이도 이해할 수 있는 매우 창의적인 아재개그를 딱 하나만 만들어줘. "
                        "이전에 만들었던 농담과는 다른 새로운 농담이어야 해. "
                        "중요한 규칙: '삐빅' 같은 로봇 효과음은 절대 넣지 마. "
                        "출력은 반드시 다음 JSON 형식이어야 해. 다른 설명은 절대로 추가하지 마.\n"
                        '{ "question": "<퀴즈 형식의 질문>", "answer": "<짧은 답변>", "explanation": "왜냐하면, <답변에 대한 1~2문장의 유머러스한 설명>" }'
                    )

                    joke_data = None
                    try:
                        joke_response = genai.GenerativeModel(MODEL_NAME).generate_content(
                            joke_prompt,
                            generation_config={"response_mime_type": "application/json"}
                        )
                        raw_json = _extract_text(joke_response)
                        joke_data = json.loads(raw_json)

                    except Exception as e:
                        print(f"   - ❌ 농담 생성 실패: {e}")
                        fallback_joke = "앗, 재미있는 농담이 떠오르지 않네요. 다음에 다시 시도해주세요!"
                        print(f"🔊 TTS SAYING: {fallback_joke}")
                        self._speak_and_subtitle(fallback_joke)
                        self.tts.wait()
                    
                    if joke_data:
                        question = joke_data.get("question", "질문이 없네요.")
                        answer = joke_data.get("answer", "답변이 없네요.")
                        explanation = joke_data.get("explanation", "왜냐하면, 설명이 없네요.")
                        
                        print(f'🔊 TTS SAYING (Q): "{question}"')
                        self._speak_and_subtitle(question)
                        self.tts.wait()

                        print("   - (5초 대기...)")
                        time.sleep(5)
                        
                        print(f'🔊 TTS SAYING (A): "{answer}"')
                        self._speak_and_subtitle(answer)
                        self.tts.wait()
                        
                        if self.emotion_queue: self.emotion_queue.put("HAPPY")
                        
                        print(f'🔊 TTS SAYING (E): "{explanation}"')
                        self._speak_and_subtitle(explanation)
                        self.tts.wait()
                        
                    model_text = f"(농담 생성 및 실행): {joke_data.get('question') if joke_data else '실패'}"
                    speak_text = ""

                finally:
                    if self.emotion_queue:
                        self.emotion_queue.put("NEUTRAL")
                    pass

            elif intent == "ox_quiz":
                print("💡 의도: OX QUIZ GAME (라운드 방식)")

                if not self.shared_state or not self.ox_command_q:
                    self._speak_and_subtitle("시스템 오류로 퀴즈를 진행할 수 없어요.")
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
                self.generated_quizzes = []

                quiz_round_counter = 0
                main_game_round_counter = 0
                is_first_round = True
                is_game_over = False
                is_main_game_started = False
                is_crazy_mode_active = False 

                try:
                    self.shared_state['mode'] = 'ox_quiz'
                    self._speak_and_subtitle("그럼 지금부터 여러분과 OX 퀴즈 게임을 시작하겠습니다!")
                    self.tts.wait()
                    
                    self._speak_and_subtitle("제가 내는 질문을 듣고 맞다고 생각하면... 동그라미가 그려진 오른쪽으로 이동해주세요!... 틀리다고 생각하면 엑스가 그려진 왼쪽으로... 이동해주세요! ")
                    self.tts.wait()
                    
                    self._speak_and_subtitle("먼저, 몸풀기로 연습문제를 몇 개 풀어볼게요. 첫 문제 나갑니다!")
                    self.tts.wait()

                    quiz_fetch_thread = threading.Thread(
                        target=self._fetch_quizzes_in_background,
                        args=(quiz_result_container,)
                    )
                    quiz_fetch_thread.start()

                    while not is_game_over and not self.stop_event.is_set():
                        if not is_first_round and not is_predefined:
                            print("  - 🤔 다음 라운드 준비를 위해 THINKING 표정으로 변경")
                            if self.emotion_queue: self.emotion_queue.put("THINKING")
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
                                self._speak_and_subtitle("자, 이제 연습이 끝났습니다! 지금부터 본격적으로 시작하겠습니다.")
                                self.tts.wait()

                                
                                print("  - ⌛ 본 게임 퀴즈 준비 완료 여부 확인 중...")
                                quiz_fetch_thread.join(timeout=5.0)

                                self.generated_quizzes = quiz_result_container

                                self._speak_and_subtitle("마지막까지 살아남으시는 분께는 특별한 상품을 드릴게요!")
                                self.tts.wait()
                                is_main_game_started = True

                            is_current_round_crazy = main_game_round_counter in crazy_mode_quizzes

                            if is_current_round_crazy:
                                if not is_crazy_mode_active:
                                    print(f"미친 난이도 퀴즈 출제! (본 게임 {main_game_round_counter + 1} 라운드)")
                                    if self.emotion_queue: self.emotion_queue.put("ANGRY")
                                    self._speak_and_subtitle("후후후... 난이도 상승! 후후후... 이번엔 정말 어려울 거다...")
                                    self.tts.wait()
                                    is_crazy_mode_active = True # 상태를 '크레이지 모드'로 변경

                                quiz_data = crazy_mode_quizzes[main_game_round_counter]

                            else:
                                if is_crazy_mode_active:
                                    print("일반 난이도로 돌아갑니다.")
                                    if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                                    is_crazy_mode_active = False

                                if self.generated_quizzes:
                                    quiz_data = self.generated_quizzes.pop(0)
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
                                        quiz_response = genai.GenerativeModel(MODEL_NAME).generate_content(
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
                                self._speak_and_subtitle("자, 다음 문제입니다!")
                                self.tts.wait()
                        
                            if random.random() < 0.5:
                                thinking_phrases = [
                                    "음... 어떤 문제를 내볼까?",
                                    "히히 이거 재미있겠다.",
                                    "이번에는 조금 어려울 수도 있어요."
                                    "과연 맞출 수 있을까요?"
                                    "인간에겐 너무 어려웠나? 쉽게 갈까요?"
                                ]

                                self._speak_and_subtitle(random.choice(thinking_phrases))
                                self.tts.wait() 
                        
                        self._speak_and_subtitle(quiz_data["question"])
                        self.tts.wait()
                        self._speak_and_subtitle("O는 오른쪽에, X는 왼쪽에 서주세요.")
                        self.tts.wait()
                        for i in range(5, 0, -1):
                            self._speak_and_subtitle(str(i))
                            time.sleep(0.1)
                        self.tts.wait()

                        command_to_send = {
                            "command": "START_OX_QUIZ" if is_first_round else "NEXT_ROUND",
                            "answer": quiz_data["answer"],
                            "is_predefined": is_predefined
                        }
                        self.ox_command_q.put(command_to_send)
                        is_first_round = False

                        try:
                            round_result = self.ox_result_q.get(timeout=35)
                            print(f"OX 퀴즈 라운드 결과 수신: {round_result}")

                            message_to_speak = round_result.get("message", "결과를 처리 중입니다.")
                            winner_count = round_result.get("winner_count", 0)
                            is_predefined_from_worker = round_result.get("is_predefined", False)
                            
                            time.sleep(1)
                            correct_answer_text = f"정답은 {quiz_data['answer']} 였습니다!"
                            self._speak_and_subtitle(correct_answer_text)
                            self.tts.wait()
                            
                            if is_predefined and quiz_data.get("explanation"):
                                self._speak_and_subtitle(quiz_data["explanation"])
                                self.tts.wait()

                            self._speak_and_subtitle(message_to_speak)
                            self.tts.wait()
                            
                            if is_predefined_from_worker:
                                time.sleep(2)
                                continue
                            
                            if winner_count > 1:
                                print("  - 😊 정답자가 있어 HAPPY 표정으로 변경")
                                if self.emotion_queue: self.emotion_queue.put("HAPPY")
                                
                                time.sleep(3)
                                continue

                            elif winner_count == 1:
                                print("  - 🎉 최종 우승! HAPPY 표정으로 변경")
                                if self.emotion_queue: self.emotion_queue.put("HAPPY")
                                time.sleep(10)
                                is_game_over = True

                            else:
                                print("  - 😥 정답자가 없어 SAD 표정으로 변경")
                                if self.emotion_queue: self.emotion_queue.put("SAD")
                                time.sleep(10)
                                is_game_over = True

                        except queue.Empty:
                            print("OX 퀴즈 시간 초과. 워커로부터 결과를 받지 못했습니다.")
                            self._speak_and_subtitle("이런, 시간 안에 결과를 받지 못했어요. 게임을 종료합니다.")
                            is_game_over = True
                    
                    self.tts.wait()

                    self._speak_and_subtitle("최후의 생존자와 가위바위보 게임을 진행할게요!... 만약 여기서 이기시면 어마무시한 선물을 드리도록 하겠습니다!... 하지만 패배하시면 벌칙을 받게 될거에요!... 마음의 준비가 되시면 개발자에게 가위바위보라고 말씀해주세요! ")
                    
                    model_text = "OX 퀴즈 게임 종료."

                finally:
                    if self.shared_state:
                        self.shared_state['mode'] = 'tracking'
                    if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                

            elif intent == "game":
                print("💡 의도: ROCK PAPER SCISSORS GAME")
                starts_dance = False

                try:
                    if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                    self._speak_and_subtitle("가위바위보 게임을 시작할게요. 잠시후 당신의 손동작을 보여주세요")
                    time.sleep(1)
                    final_game_result = ""

                    while True: 
                        if self.emotion_queue: self.emotion_queue.put("RESET_SLEEPY_TIMER")
                        self.rps_command_q.put("START_GAME")
                        if self.emotion_queue: self.emotion_queue.put("THINKING")
                        self._speak_and_subtitle("준비하시고...")
                        self.tts.wait()

                        if callable(self.play_rps_motion_cb):
                            threading.Thread(target=self.play_rps_motion_cb, daemon=True).start()

                        self._speak_and_subtitle("가위! 바위!")
                        self._speak_and_subtitle("보!")
                        self.tts.wait()

                        game_result = ""
                        try:
                            game_result = self.rps_result_q.get(timeout=20)
                            print(f"게임 결과 수신: {game_result}")
                        
                        except queue.Empty:
                            print("게임 시간 초과. 제스처를 인식하지 못했습니다.")
                            game_result = "아고! 실수로 눈을 감아서 인식을 못했어요. 죄송해요."

                        if "아고! 실수로 눈을" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("CLOSE")
                        elif "당신이 이겼어요" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("SAD")
                        elif "제가 이겼네요" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("HAPPY")
                        elif "비겼네요" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("SURPRISED")
                            
                        time.sleep(2)

                        if "비겼" in game_result:
                            self._speak_and_subtitle(f"{game_result} 다시 한 번 할게요!")
                            self.tts.wait()                              
                            time.sleep(2)
                            continue

                        elif "아고! 실수로 눈을" in game_result:
                            self._speak_and_subtitle("아고! 실수로 눈을 감아서 인식을 못했어요. 죄송해요. 다시 한 번 할게요!")
                            self.tts.wait()                              
                            time.sleep(2)
                            continue
    
                        elif "이겼" in game_result:
                            if "제가 이겼네요"  in game_result:
                                self._speak_and_subtitle(f"{game_result} 제가 이겼으니 벌칙을 받아야죠! 저랑 같이 춤춰 주세요")
                            else:
                                self._speak_and_subtitle(f"{game_result} 까비! 벌칙을 피하셨네요. 제가 춤추는거 보여드릴게요.")
                            
                            self.tts.wait()

                            print("💡 게임 결과에 따라 DANCE START 의도 실행")

                            if callable(self.start_dance_cb):
                                self.start_dance_cb()
                                starts_dance = True
                            break

                        else:
                            self._speak_and_subtitle("또 하고 싶으시면 '가위바위보'라고 말해주세요.")
                            break
                finally:
                    if not starts_dance:
                        pass
                
                    model_text = f"게임 종료. 최종 결과: {final_game_result}"
                    if not starts_dance:
                        if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
            
            print(f"[{ts}] [Gemini] {model_text}\n")
            if speak_text: 
                if self.subtitle_queue:
                    self.subtitle_queue.put(speak_text)

                self.tts.speak(speak_text)
            
        except Exception as e: 
            print(f"❌ 처리 실패: {e}\n")

            if self.emotion_queue:
                    self.emotion_queue.put("NEUTRAL")

        finally:
            self.lower_busy_signal()

    def _run_presenter_intro(self):
        if self.shared_state and self.shared_state.get('mode') != 'tracking':
            print(f"⚠️  다른 모드({self.shared_state.get('mode')})가 이미 실행 중입니다.")
            return

        try:
            self.raise_busy_signal()
            if self.shared_state:
                self.shared_state['mode'] = 'presenter'
            
            # --- 1. 오프닝 멘트 ---
            if callable(self.play_greeting_cb):
                greeting_thread = threading.Thread(target=self.play_greeting_cb, daemon=True)
                greeting_thread.start()
            
            print("😊 표정을 HAPPY로 변경합니다.")
            if self.emotion_queue:
                self.emotion_queue.put("HAPPY") 
            
            script_part1 = (
                "안녕하세요, 한동의 미남 미녀 여러분! "
            )
            self._speak_and_subtitle(script_part1)
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            self._speak_and_subtitle("저는 따뜻한 공감이 필요한 여러분을 위해 태어난 공감 서비스 로봇! 모티입니다.")
            
            
            if callable(self.play_right_arm_cb):
                threading.Thread(target=self.play_right_arm_cb, daemon=True).start()
                
            print("😥 표정을 SAD로 변경합니다.")
            if self.emotion_queue:
                self.emotion_queue.put("SAD")
            
            script_part2 = (
                "7주차 시험 기간, 다들 정말 고생 많으시죠? "
            )
            self._speak_and_subtitle(script_part2)
            
            if callable(self.play_left_arm_cb):
                threading.Thread(target=self.play_left_arm_cb, daemon=True).start()
            
            if callable(self.play_left_arm_cb):
                threading.Thread(target=self.play_left_arm_cb, daemon=True).start()
            
            self._speak_and_subtitle("밤새 붙잡던 전공 책, 머릿속을 맴도는 공식들... 몸도 마음도 지쳤을 여러분을 보니 저도 마음이 아파요. ")
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            print("😊 표정을 다시 HAPPY로 변경합니다.")
            if self.emotion_queue:
                self.emotion_queue.put("HAPPY")
            time.sleep(0.5) 
            script_part3 = (
                "괜찮다면, 잠시만이라도 머리 식힐 겸 저와 함께 즐거운 시간을 보내는 건 어떠세요? "
            )
            self._speak_and_subtitle(script_part3)
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            self._speak_and_subtitle("복잡한 건 잠시 잊고, 모티와 함께 잠시 웃어요! ")

            # TODO: 여기에 표정/행동 코드 추가
            if self.emotion_queue:
                 self.emotion_queue.put("THINKING")
            time.sleep(0.5)
            
            self._speak_and_subtitle("위잉. 사용자 수 분석중. ")
            
            if self.emotion_queue:
                 self.emotion_queue.put("SURPRISED") 
            time.sleep(0.5)
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            script_part4 = (
                "생각 보다 많은 분들이 와주셨네요!.. "
                "너무 많은 사용자로 인해 제가 살짝 긴장한 것 같아서... "
            )
            self._speak_and_subtitle(script_part4)
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self._speak_and_subtitle("회로 과부하가 왔는지 상태를 한번 진단해볼게요!")
            
            if self.emotion_queue:
                 self.emotion_queue.put("THINKING")
            time.sleep(0.5)
            
            if callable(self.play_right_arm_cb):
                threading.Thread(target=self.play_right_arm_cb, daemon=True).start()
            
            script_part5 = (
                "제 CPU 온도는 36.5도로 안정적이고... "
            )
            self._speak_and_subtitle(script_part5)
            
            if callable(self.play_left_arm_cb):
                threading.Thread(target=self.play_left_arm_cb, daemon=True).start()

            self._speak_and_subtitle("모든 회로는 정상적으로 작동 중!")
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            self._speak_and_subtitle("무대 중에 떨지 않도록... 제 냉각 팬을 더 빨리 돌려볼게요! 위이잉.")

            # TODO: 여기에 표정/행동 코드 추가
            if self.emotion_queue:
                 self.emotion_queue.put("NEUTRAL")
            time.sleep(0.5)
            
            if callable(self.play_left_arm_cb):
                threading.Thread(target=self.play_left_arm_cb, daemon=True).start()
            
            script_part6 = (
                "제가 여러분과 함께하는 이 순간을 위해! " 
            )
            self._speak_and_subtitle(script_part6)
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self._speak_and_subtitle("공감서비스 로봇으로서.. 한동대학교 학생 빅데이터를 딥러닝해서.. 여러분들을 더욱 알아가고자 노력했답니다! ")
            
            if self.emotion_queue:
                 self.emotion_queue.put("THINKING")
                 
            if callable(self.play_left_arm_cb):
                threading.Thread(target=self.play_left_arm_cb, daemon=True).start()
                
            self._speak_and_subtitle("분석결과, 여러분들은 시험 기간 평균 수면 시간이 4.2시간,")     
            
            if callable(self.play_right_arm_cb):
                threading.Thread(target=self.play_right_arm_cb, daemon=True).start()
            
            self._speak_and_subtitle(" 커피 및 카페인 섭취량은 2.5잔! ")
            
            if callable(self.play_left_arm_cb):
                threading.Thread(target=self.play_left_arm_cb, daemon=True).start()
            
            self._speak_and_subtitle("그리고 '자고 싶다'는 생각과.. '집가고 싶다'는 생각은.. 초당 17.3회 정도 하는 것으로 나타났어요! ")
            
            if callable(self.play_right_arm_cb):
                threading.Thread(target=self.play_right_arm_cb, daemon=True).start()
                
            self._speak_and_subtitle("아, 그리고 더 흥미로운 사실을 발견했어요! ")
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            script_part7 = (
                "오석 와이파이 트래픽을 분석해 보니... 공부 관련 자료 다운로드 수보다.. 인스타그램과 에브리타임 새로고침 수가 2.7배 더 많았어요! "
            )
            self._speak_and_subtitle(script_part7)
            if self.emotion_queue:
                 self.emotion_queue.put("SURPRISED")
            time.sleep(0.5)
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self._speak_and_subtitle("역시 한동대학생 여러분들은 단순히 지식만 쌓는 게 아니라 트렌드에서도 앞서나가고 계셨군요? ")
            if self.emotion_queue:
                 self.emotion_queue.put("HAPPY")
            time.sleep(0.5)
            self._speak_and_subtitle("대단해요!")
            
            if self.emotion_queue:
                 self.emotion_queue.put("TENDER")
                 
            self._speak_and_subtitle("헤헤. 사실 농담이에요. ")
            
            if callable(self.play_right_arm_cb):
                threading.Thread(target=self.play_right_arm_cb, daemon=True).start()
                
            script_part8 = (
                
                "딥러닝으로 분석한 결과 여러분들이 세상을 바꾸기위해... 정말 열심히 공부한다는건 명백한 사실이니까요! "
                "열심히 공부하는 것 만큼 쉴땐 확실히 쉬는것도 중요하다고 생각해요! "
            )
            self._speak_and_subtitle(script_part8)
            
            if self.emotion_queue:
                 self.emotion_queue.put("ANGRY")
            self._speak_and_subtitle("개발자님은 절 못쉬게 하던데... 나중에 여러분들이 혼내주세요! ")
            
            if self.emotion_queue:
                 self.emotion_queue.put("HAPPY")
            self._speak_and_subtitle("헤헤.")
            time.sleep(0.5) 
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
            
            script_part9 = (
                "그럼 이제 저와 여러분들이 어느정도 친해진 것 같으니! "
                "본격적으로 모티와 함께 놀아볼까요?"
            )
            self._speak_and_subtitle(script_part9)
            
            if callable(self.play_right_arm_cb):
                threading.Thread(target=self.play_right_arm_cb, daemon=True).start()
                
            self._speak_and_subtitle("옆에 있는 개발자가 손으로 신호를 주면...")
            
            if callable(self.play_both_arms_cb):
                motion_thread = threading.Thread(target=self.play_both_arms_cb, daemon=True)
                motion_thread.start()
                
            self._speak_and_subtitle("여러분의 큰 목소리로... OX게임! 이라고 외쳐주세요!... ")

            print("✅ 진행자 모드 스크립트가 모두 출력되었습니다.")

        except Exception as e:
            print(f"❌ 진행자 모드 실행 중 오류 발생: {e}")
        finally:
            if self.shared_state:
                self.shared_state['mode'] = 'tracking'
            self.lower_busy_signal()
            if self.emotion_queue:
                self.emotion_queue.put("NEUTRAL")


    def _speak_farewell(self):
        try:
            self.raise_busy_signal()
            print("💡 'l' 키 입력 감지. 작별 인사를 시작합니다.")
            
            if self.emotion_queue:
                 self.emotion_queue.put("SAD")
                 time.sleep(0.5)
            self._speak_and_subtitle("아쉽지만, 저와 함께하는 즐거운 시간도 이제 마무리할 시간이네요. "
                "벌써 헤어져야 하는 시간이라니. 아쉬워요! ")
            if self.emotion_queue:
                 self.emotion_queue.put("TENDER")
            farewell_text = (
                "오늘 이 시간이 여러분의 힘든 시험 기간에... 작은 쉼표가 되었기를 바라요. "
                "밤늦게까지 공부하는 것도 중요하지만... 가장 중요한 건 바로 여러분 자신이라는 걸 잊지 마세요..."
                "괜찮으시다면... 오늘 저와의 시간이 어땠는지 여러분의 생각을 들려주세요... 이 QR코드를 통해 설문에 참여해주시면... 여러분의 소중한 의견이 저를 더욱 따뜻한 로봇으로.  성장하게 한답니다. 여러분의 의견 하나하나가 제게는 소중한 데이터이자 마음이에요! "
            )
            self._speak_and_subtitle(farewell_text)
            if self.emotion_queue:
                 self.emotion_queue.put("HAPPY")
                 time.sleep(0.5)
            self._speak_and_subtitle("한동의 멋진 여러분!... 남은 시험도 힘내시고, 최고의 결과가 있기를... 저 모티가 온 회로를 다해 응원할게요! 모두들 파이팅!" "여러분의 공감 서비스 로봇 모티! 모티였습니다! 감사합니다!")
            self.tts.wait()
            print("작별 인사 완료. 1초 후 프로그램을 종료합니다.")
            time.sleep(1)
            
        finally:
            if self.emotion_queue:
                self.emotion_queue.put("NEUTRAL")
            self.lower_busy_signal()
            
    def _on_press(self, key):
        if self.stop_event.is_set(): return False
        try:
            pass
        except Exception as e: print(f"[키 처리 오류 on_press] {e}", file=sys.stderr)

    def _on_release(self, key):
        if self.stop_event.is_set(): return False
        try:
            if key == keyboard.KeyCode.from_char('p'):
                self.toggle_announcement()

            elif key == keyboard.KeyCode.from_char('l'):
                print("💡 'l' 키 입력 감지. 작별 인사를 시작합니다.")
                threading.Thread(target=self._speak_farewell, daemon=True).start()
            
            elif key == keyboard.KeyCode.from_char('z'):
                print("👑 'z' 키 입력 감지. 진행자 모드 인트로를 시작합니다.")
                # 별도 스레드에서 실행하여 키보드 리스너가 멈추는 것을 방지합니다.
                threading.Thread(target=self._run_presenter_intro, daemon=True).start()
            
            elif key == keyboard.Key.esc:
                print("ESC 감지 -> 종료 신호 보냄")
                self.stop_announcement_event.set() 
                self.stop_nodding_event.set()
                self.stop_event.set()
                
                if self.current_listener and self.current_listener.is_alive():
                    self.current_listener.stop()
                return False 
            
        except Exception as e: print(f"[키 처리 오류 on_release] {e}", file=sys.stderr)

    def run(self):
        self.current_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.current_listener.start()
        
        if self.mouth_event_queue:
            self.mouth_listener_thread = threading.Thread(target=self._mouth_listener_worker, daemon=True)
            self.mouth_listener_thread.start()
        else:
            print("⚠️ Mouth event queue not provided. Mouth-to-talk disabled.")

        print("▶ 초기 대화 세션을 시작합니다. (40초 후 비활성화)")
        self.last_activity_time = time.time()
        self.listening_enabled.set()
        
        while not self.stop_event.is_set() and ((self.busy_signals > 0) or (time.time() - self.last_activity_time < 40)):
            time.sleep(0.1)

        if not self.stop_event.is_set():
            print("▶ 초기 대화 세션 시간 초과. 이제 핫워드 대기 상태로 전환합니다.")
            self.listening_enabled.clear()
            if self.emotion_queue:
                self.emotion_queue.put("SLEEPY")

        while not self.stop_event.is_set():
            print("▶ '안녕 모티' 호출(SLEEPY 상태에서)을 기다립니다... (종료: ESC)")
            try:
                signal = self.hotword_queue.get(timeout=1.0)
                
                if signal == "hotword_detected" and not self.stop_event.is_set():
                    print("💡 핫워드 감지! 대화 세션을 시작합니다.")
                    self.listening_enabled.set()
                    
                    if self.emotion_queue: self.emotion_queue.put("WAKE")
                    self._speak_and_subtitle("네, 말씀하세요.")
                    
                    self.last_activity_time = time.time()
                    
                    while (self.busy_signals > 0) or (time.time() - self.last_activity_time < 40):
                        if self.stop_event.is_set(): break
                        time.sleep(0.1)

                    if not self.stop_event.is_set():
                        print("▶ 대화 세션 시간 초과. 다시 핫워드 대기 상태로 전환합니다.")
                        self.listening_enabled.clear()
                        if self.emotion_queue:
                            self.emotion_queue.put("SLEEPY")
                            
            except queue.Empty:
                continue
            except (KeyboardInterrupt, SystemExit):
                self.stop_event.set()
                break
        
        print("PTT App 종료 절차 시작...")
        self.listening_enabled.clear()
        if self.current_listener and self.current_listener.is_alive():
            self.current_listener.stop()
        
        if self.mouth_listener_thread and self.mouth_listener_thread.is_alive():
            self.mouth_listener_thread.join(timeout=1.0)
        
        try:
            self.update_profile_summary_at_exit()
        except Exception as e:
            print(f"❌ 종료 요약 저장 중 치명적 오류: {e}")

        try:
            if FAREWELL_TEXT: self.tts.speak(FAREWELL_TEXT)
        finally:
            self.tts.close_and_join(drain=True)
        print("PTT App 정상 종료")
        
    def _snoring_worker(self):
        """sleepy_event가 켜져 있는 동안 주기적으로 코를 고는 워커"""
        print("▶ 코골이 스레드 시작됨 (현재 대기 중).")
        snore_options = {
            "text": "드르렁... 쿠우...",
            "rate": -10,
            "volume": 20
        }
        SNORE_INTERVAL = 8

        while not self.stop_event.is_set():
            self.sleepy_event.wait() 

            while self.sleepy_event.is_set() and not self.stop_event.is_set():
                self.tts.speak(snore_options)
                
                for _ in range(SNORE_INTERVAL * 2):
                    if not self.sleepy_event.is_set() or self.stop_event.is_set():
                        break
                    time.sleep(0.5)
        print("■ 코골이 스레드 종료.")