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
import re # 스트리밍 응답 처리를 위해 re 임포트
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Callable
import multiprocessing
from functools import wraps

from function.entertain import EntertainmentHandler
from function.present import PresentationHandler
from function.profile_manager import ProfileManager
from function.utils import _get_relative_time_str, _extract_text, _get_env, SYSTEM_INSTRUCTION

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

# --- 전역 상수는 그대로 둠 ---
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

TTS_RATE = int(_get_env("TTS_RATE", "0"))
TTS_VOLUME = int(_get_env("TTS_VOLUME", "100"))
TTS_FORCE_VOICE_ID = _get_env("TTS_FORCE_VOICE_ID", "")
TTS_OUTPUT_DEVICE = _get_env("TTS_OUTPUT_DEVICE", "")
GREETING_TEXT = _get_env("GREETING_TEXT", "안녕하세요! 모티입니다.")
FAREWELL_TEXT = _get_env("FAREWELL_TEXT", "도움이 되었길 바라요. 언제든 다시 불러주세요.")
ENABLE_GREETING = _get_env("ENABLE_GREETING", "1") not in ("0", "false", "False")


@dataclass
class RecorderState:
    recording: bool = False
    frames_q: queue.Queue = queue.Queue()
    stream: sd.InputStream | None = None

# --- TTS Worker 클래스들은 그대로 둠 ---
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

# --- 메인 PressToTalk 클래스 (컨트롤러) ---
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
                 perform_head_nod_cb: Optional[Callable[[int], None]] = None
                 ): 
        
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key or not api_key.strip():
            print("❗ GOOGLE_API_KEY가 없습니다."); sys.exit(1)

        genai.configure(api_key=api_key)
        self.MODEL_NAME = MODEL_NAME
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
        
        # ▼▼▼ [수정] 프로필 관련 변수 초기화 (ProfileManager가 사용) ▼▼▼
        self.current_user_name = None
        self.profile_db_file = PROFILE_DB_FILE
        self.initial_chat_summary = "아직 기록된 내용이 없습니다."
        self.initial_last_seen_str = "기록 없음"
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        # --- 콜백 및 큐 저장 (기존과 동일) ---
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

        # --- TTS 및 상태 초기화 (기존과 동일) ---
        default_engine = "sapi" if IS_WINDOWS else "typecast"
        engine = _get_env("TTS_ENGINE", default_engine).lower()
        if engine == "sapi" and not IS_WINDOWS: engine = "typecast"
        if engine == "typecast": self.tts = TypecastTTSWorker()
        else: self.tts = SapiTTSWorker()
        self.tts.start()

        self.state = RecorderState()
        self._print_intro()

        # ▼▼▼ [추가] 핸들러 클래스 초기화 (반드시 self.xxx 변수 설정 *이후*에) ▼▼▼
        self.entertain_handler = EntertainmentHandler(self)
        self.present_handler = PresentationHandler(self)
        self.profile_manager = ProfileManager(self)

        # ProfileManager에게 DB 초기화를 위임
        self.profile_manager.init_db()
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
        
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

    # --- [삭제] _init_profile_db, load_profile, update_profile_summary_at_exit ---
    # (이 함수들은 profile_manager.py로 이동함)

    # --- [삭제] _announcement_worker, toggle_announcement, _run_presenter_intro, _speak_farewell ---
    # (이 함수들은 present.py로 이동함)
    
    # --- [유지] _fetch_quizzes_in_background (EntertainmentHandler가 사용) ---
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
            quiz_response = genai.GenerativeModel(self.MODEL_NAME).generate_content(
                quiz_prompt, 
                generation_config={"response_mime_type": "application/json"}
            )
            raw_json = _extract_text(quiz_response)
            quizzes = json.loads(raw_json)
            result_container.extend(quizzes)
            print(f"  - ✅ (백그라운드) 퀴즈 {len(quizzes)}개 생성 완료!")
        except Exception as e:
            print(f"  - ❌ (백그라운드) 퀴즈 생성 실패: {e}")  

    # --- [유지] _listening_nod_worker (경청 끄덕임) ---
    def _listening_nod_worker(self):
        """사용자가 말하는 동안 랜덤하게 고개를 끄덕이는 백그라운드 스레드"""
        print("👂 경청 모드: 랜덤 끄덕임 스레드 시작...")
        
        start_wait = random.uniform(0.5, 1.5)
        interrupted = self.stop_nodding_event.wait(timeout=start_wait)
        if interrupted:
            print("👂 경청 모드: 시작 전 중지됨.")
            return

        while not self.stop_nodding_event.is_set():
            if random.random() < 0.3: # [수정] 20% -> 30% 확률
                reps = 2
                print("👂 (경청) 끄덕임 x2")
            else:
                reps = 1
                print("👂 (경청) 끄덕임 x1")

            if callable(self.perform_head_nod_cb):
                try:
                    threading.Thread(target=self.perform_head_nod_cb, args=(reps,), daemon=True).start()
                except Exception as e:
                    print(f"⚠️ 경청 끄덕임 중 오류: {e}")
            
            wait_time = random.uniform(1.5, 4.0)
            interrupted = self.stop_nodding_event.wait(timeout=wait_time)
            
            if interrupted:
                break
        
        print("👂 경청 모드: 랜덤 끄덕임 스레드 종료.")

    # --- [유지] 핵심 기능 함수들 ---
    def _mouth_listener_worker(self):
        """A dedicated thread to listen for mouth events."""
        print("▶ 🔊 Mouth-to-Talk listener thread started.")
        while not self.stop_event.is_set():
            try:
                msg = self.mouth_event_queue.get(timeout=0.2) 
                
                if msg == "START_RECORDING":
                    if self.listening_enabled.is_set():
                        if self.busy_signals > 0:
                            print(f"👄 게임/말하는 중 말 인식 멈춤 (busy_signals: {self.busy_signals})")
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
        - 말하는 동안 busy_signals를 올려 스스로의 말을 재녹음하는 것을 방지합니다.
        """
        import re

        if not text_data:
            return

        try:
            if isinstance(text_data, dict):
                text_to_display = text_data.get("text", "")
                if self.subtitle_queue and text_to_display:
                    self.subtitle_queue.put(text_to_display)
                self.tts.speak(text_data)
                # dict는 wait() 안 함 (스노링 등 비동기 사운드용)
                return 
            
            text_to_process = str(text_data)
            # 문장 분리기: 마침표, 물음표, 느낌표 뒤의 공백을 기준으로 자름
            sentences = re.split(r'(?<=[.!?])\s+', text_to_process)
            sentences = [s.strip() for s in sentences if s.strip()]

            if not sentences:
                if text_to_process.strip(): # 분리 실패 시 통째로 말함
                    sentences = [text_to_process.strip()]
                else:
                    return # 빈 텍스트면 종료

            for sentence in sentences:
                if self.subtitle_queue:
                    self.subtitle_queue.put(sentence)
                
                self.tts.speak(sentence)
                self.tts.wait() # 각 문장이 끝날 때까지 기다림
        finally:
            pass

    def _print_intro(self):
        print("\n=== Gemini PTT (통합 버전) ===")
        print("▶ '안녕 모티'로 호출(SLEEPY 상태) → 입 열기로 대화(NEUTRAL 상태) → ESC로 종료")
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
        try:
            self.state.frames_q.put_nowait(indata.copy())
        except queue.Full:
            pass # 큐가 꽉 차면(녹음 중이 아닐 때) 오디오 데이터를 버림

    def _start_recording(self):
        if self.state.recording: return
        if self.emotion_queue:
            self.emotion_queue.put("RESET_SLEEPY_TIMER")
            self.emotion_queue.put("LISTENING") # [수정] 경청 표정으로

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
            self.emotion_queue.put("THINKING") # [수정] 생각 표정으로
        self.last_activity_time = time.time()
        print("✅ User stopped speaking. Activity timer reset.")
        print("⏹️  녹음 종료, 전사 중...")
        self.state.recording = False
        try:
            if self.state.stream: self.state.stream.stop(); self.state.stream.close()
        finally: self.state.stream = None
        
        self.stop_nodding_event.set()
        
        chunks = []
        while not self.state.frames_q.empty(): 
            try:
                chunks.append(self.state.frames_q.get_nowait())
            except queue.Empty:
                break
                
        if not chunks: 
            print("(녹음 데이터가 없습니다.)\n")
            if self.emotion_queue: self.emotion_queue.put("NEUTRAL") # [추가] 녹음 실패 시 NEUTRAL로
            return
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
                    self.profile_manager.load_profile_for_chat(name)
                else:
                    print("⚠️  의도: INTRODUCTION (이름 추출 실패). Chat으로 폴백.")
                    intent = "chat"
                    is_chat_intent = True

            if not is_chat_intent:
                    if self.emotion_queue:
                        print("... Introduction 완료, NEUTRAL로 표정 리셋 ...")
                        self.emotion_queue.put("NEUTRAL")

            model_text, speak_text = "", ""

            if intent == "chat":
                # ▼▼▼ [수정] AI 답변 스트리밍 적용 ▼▼▼
                print(f"[{ts}] [Gemini] 응답 스트리밍 시작...")
                reply_stream = self.chat.send_message(user_text, stream=True)
                
                speak_text = ""
                full_model_text = ""
                current_sentence = ""
                is_first_chunk = True

                for chunk in reply_stream:
                    chunk_text = _extract_text(chunk)
                    if not chunk_text: continue

                    full_model_text += chunk_text
                    current_sentence += chunk_text

                    if is_first_chunk and current_sentence.strip():
                        print(f"  -> First chunk received, analyzing emotion...")
                        self._analyze_and_send_emotion(current_sentence)
                        is_first_chunk = False

                    # 문장 종결 부호(.!?) 또는 줄바꿈(\n)을 만나면, 해당 문장을 즉시 말함
                    if any(c in current_sentence for c in ".!?\n"):
                        # 문장 종결 부호 기준으로 문장 분리 (정규표현식 사용)
                        sentences_to_speak = re.split(r'(?<=[.!?\n])\s*', current_sentence)
                        
                        if len(sentences_to_speak) > 1:
                            # 마지막 조각(아직 완성되지 않음)을 제외하고 모두 말함
                            current_sentence = sentences_to_speak.pop(-1)
                            for sentence in sentences_to_speak:
                                if sentence.strip():
                                    print(f"  -> Speaking chunk: {sentence.strip()}")
                                    self._speak_and_subtitle(sentence.strip())
                        # (else: 아직 문장 종결 부호가 나오지 않음, 다음 청크까지 대기)
                
                # 스트림이 끝나고 남은 마지막 문장 처리
                if current_sentence.strip():
                    print(f"  -> Speaking final chunk: {current_sentence.strip()}")
                    self._speak_and_subtitle(current_sentence.strip())

                model_text = full_model_text # 요약을 위해 전체 텍스트 저장
                speak_text = "" # 이미 다 말했으므로 비움
                
                if self.emotion_queue:
                    print("... TTS 완료, NEUTRAL로 표정 리셋 ...")
                    self.emotion_queue.put("NEUTRAL")
                # ▲▲▲ 스트리밍 적용 끝 ▲▲▲

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
                print("💡 의도: JOKE (EntertainmentHandler로 위임)")
                self.entertain_handler.run_joke()
                speak_text = ""
                model_text = "(농담 실행)"

            elif intent == "ox_quiz":
                print("💡 의도: OX QUIZ (EntertainmentHandler로 위임)")
                self.entertain_handler.run_ox_quiz()
                speak_text = ""
                model_text = "(OX 퀴즈 실행)"
            
            elif intent == "game":
                print("💡 의도: RPS GAME (EntertainmentHandler로 위임)")
                self.entertain_handler.run_rps_game()
                speak_text = ""
                model_text = "(가위바위보 실행)"
            
            if speak_text: 
                # dance, stop 등 간단한 응답 처리
                self._speak_and_subtitle(speak_text)
            
        except Exception as e: 
            print(f"❌ 처리 실패: {e}\n")
            if self.emotion_queue:
                    self.emotion_queue.put("NEUTRAL")

        finally:
            # 1. 모든 TTS 출력이 끝날 때까지 기다립니다.
            print("... 모든 TTS 출력이 끝날 때까지 대기 중 ...")
            self.tts.wait() 
            print("... TTS 출력 완료 ...")

            # 2. [수정] 'chat' 또는 'introduction'일 경우, 백그라운드에서 '실시간 메모리' 업데이트
            if (intent == "chat" or intent == "introduction") and user_text and model_text:
                print("... (백그라운드에서 실시간 메모리 업데이트 시작)")
                try:
                    threading.Thread(
                        target=self.profile_manager.update_summary_after_chat, # <<< 새 함수 호출
                        args=(user_text, model_text), 
                        daemon=True
                    ).start()
                except Exception as e:
                    print(f"❌ 프로필 요약 스레드 시작 중 오류 발생: {e}")
            
            # 3. 모든 작업이 끝났으므로 busy signal을 낮춤
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
                self.present_handler.toggle_announcement()

            elif key == keyboard.KeyCode.from_char('l'):
                print("💡 'l' 키 입력 감지. 작별 인사를 시작합니다.")
                threading.Thread(target=self.present_handler.speak_farewell, daemon=True).start()
            
            elif key == keyboard.KeyCode.from_char('z'):
                print("👑 'z' 키 입력 감지. 진행자 모드 인트로를 시작합니다.")
                threading.Thread(target=self.present_handler.run_presenter_intro, daemon=True).start()
            
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
            # ▼▼▼ [수정] 종료 시 ProfileManager에게 위임 ▼▼▼
            self.profile_manager.save_profile_at_exit()
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