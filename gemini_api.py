# gemini_api.py
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
import re 
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Callable
import multiprocessing
from functools import wraps

from function.entertain import EntertainmentHandler
from function.present import PresentationHandler
from function.profile_manager import ProfileManager
from function.utils import _get_relative_time_str, _extract_text, _get_env, SYSTEM_INSTRUCTION

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from function.vision_brain import RobotBrain

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

# --- 전역 상수 ---
SAMPLE_RATE = int(_get_env("SAMPLE_RATE", "16000"))
CHANNELS = int(_get_env("CHANNELS", "1"))
DTYPE = _get_env("DTYPE", "int16")
MODEL_NAME = _get_env("MODEL_NAME", "gemini-3.1-flash-lite-preview")

ONE_SHOT_PROMPT = (
    "이 오디오를 전사하고 의도를 분류하며, 다음 의도 가이드라인을 따르세요.\n"
    "1. 'greeting': '안녕', '반가워' 등 인사.\n"
    "2. 'shy': '귀여워', '똑똑해', '이쁘다' 등 칭찬.\n"
    "3. 'hug': '안아줘', '포옹해줘' 등 스킨십 요청.\n"
    "4. 'comfort': '힘들어', '속상해', '위로해줘' 등 위로 요청.\n"
    "5. 'introduction': 사용자가 이름을 말할 때. (name 필드에 이름 추출)\n"
    "6. 'ox_quiz': 'OX 퀴즈', '퀴즈 하자', '문제 내줘' 등 퀴즈 요청.\n"
    "7. 'game': '가위바위보', '게임 하자' 등 게임 요청.\n"
    "8. 'joke': '농담해줘', '개그 해줘', '재밌는 얘기' 등 유머 요청.\n"
    "9. 'dance': '춤춰줘', '댄스' 등 춤 요청.\n"
    "10. 'stop': '그만', '멈춰' 등 중단 요청.\n"
    "그 외 일상 대화는 'chat'으로 분류하세요.\n"
    "답변(reply)은 'chat', 'greeting', 'shy', 'hug', 'comfort'일 때만 1~2문장으로 따뜻하게 작성하고, "
    "나머지 기능 실행 의도(ox_quiz, game, dance, joke, stop)일 때는 reply를 빈 문자열(\"\")로 두세요.\n"
    "반드시 다음 JSON 형식으로만 출력하세요: "
    '{"text": "전사된 텍스트", "intent": "의도", "reply": "답변", "name": "이름(없으면 null)"}'
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

# --- TTS Worker 클래스 ---
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
                        if hasattr(self, 'subtitle_queue') and self.subtitle_queue:
                            self.subtitle_queue.put(text)

                        voice.Speak(text, 0); 
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
                        if hasattr(self, 'subtitle_queue') and self.subtitle_queue:
                            self.subtitle_queue.put(text)

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
                 play_shy_cb: Optional[Callable[[], None]] = None,
                 play_hug_cb: Optional[Callable[[], None]] = None,
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
                 perform_head_nod_cb: Optional[Callable[[int], None]] = None,
                 brain_instance = None,
                 ): 
        
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key or not api_key.strip():
            print("❗ GOOGLE_API_KEY가 없습니다."); sys.exit(1)

        genai.configure(api_key=api_key)
        self.MODEL_NAME = MODEL_NAME
        self.model = genai.GenerativeModel(MODEL_NAME)
        self.chat = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_INSTRUCTION).start_chat(history=[])
        
        # [참고] router_model은 초기화만 유지
        self.router_model = genai.GenerativeModel(
            MODEL_NAME,
            system_instruction="라우터는 이제 사용되지 않지만 구조 유지를 위해 남겨둡니다.",
            generation_config={"response_mime_type": "application/json", "temperature": 0.2}
        )
        
        self.current_user_name = None
        self.profile_db_file = PROFILE_DB_FILE
        self.initial_chat_summary = "아직 기록된 내용이 없습니다."
        self.initial_last_seen_str = "기록 없음"
        self.session_history = []

        self.start_dance_cb = start_dance_cb
        self.stop_dance_cb  = stop_dance_cb
        self.play_rps_motion_cb = play_rps_motion_cb
        self.play_greeting_cb = play_greeting_cb
        self.play_both_arms_cb = play_both_arms_cb
        self.play_right_arm_cb = play_right_arm_cb
        self.play_left_arm_cb = play_left_arm_cb
        self.play_wheel_wiggle_cb = play_wheel_wiggle_cb
        self.play_shy_cb = play_shy_cb
        self.play_hug_cb = play_hug_cb
        self.emotion_queue = emotion_queue
        self.subtitle_queue = subtitle_queue
        self.hotword_queue = hotword_queue
        self.stop_event = stop_event or threading.Event()
        
        self.brain = brain_instance
        self.last_logged_in_user = None

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
        self.tts.subtitle_queue = subtitle_queue
        self.tts.start()

        self.state = RecorderState()
        self._print_intro()

        self.entertain_handler = EntertainmentHandler(self)
        self.present_handler = PresentationHandler(self)
        self.profile_manager = ProfileManager(self)
        self.profile_manager.init_db()
        
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

    def _fetch_quizzes_in_background(self, result_container: list):
        print("   - 🏃 (백그라운드) 본 게임 퀴즈 생성을 시작합니다...")
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
            print(f"   - ✅ (백그라운드) 퀴즈 {len(quizzes)}개 생성 완료!")
        except Exception as e:
            print(f"   - ❌ (백그라운드) 퀴즈 생성 실패: {e}")  

    def _listening_nod_worker(self):
        print("👂 경청 모드: 랜덤 끄덕임 스레드 시작...")
        
        start_wait = random.uniform(0.5, 1.5)
        interrupted = self.stop_nodding_event.wait(timeout=start_wait)
        if interrupted:
            print("👂 경청 모드: 시작 전 중지됨.")
            return

        while not self.stop_nodding_event.is_set():
            if random.random() < 0.3: 
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

    def _mouth_listener_worker(self):
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
        if not text_data:
            return

        try:
            # 1. 텍스트 추출
            if isinstance(text_data, dict):
                text_to_display = text_data.get("text", "")
            else:
                text_to_display = str(text_data).strip()

            if not text_to_display:
                return

            # 👉 [수정] 터미널(콘솔)에 모티의 말을 출력하도록 추가!
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 🗣️ 모티: {text_to_display}")
            
            # 👉 TTS도 한 번에 통째로 읽도록 처리
            self.tts.speak(text_data)
            self.tts.wait() 
            
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
            pass 

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
        while not self.state.frames_q.empty(): 
            try:
                chunks.append(self.state.frames_q.get_nowait())
            except queue.Empty:
                break
                
        if not chunks: 
            print("(녹음 데이터가 없습니다.)\n")
            if self.emotion_queue: self.emotion_queue.put("NEUTRAL") 
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
            if intent not in ("dance", "stop", "game", "chat", "joke", "ox_quiz", "introduction", "greeting", "shy"): intent = "chat"
            return {"intent": intent, "normalized_text": str(data.get("normalized_text", text)), "speakable_reply": str(data.get("speakable_reply", "")) if intent == "chat" else "", "name": data.get("name")}
        except Exception as e:
            print(f"(router 폴백) {e}")
            low = text.lower()
            
            if any(w in low for w in ["안녕", "반가워", "하이", "hello", "hi"]): 
                return {"intent": "greeting", "normalized_text": text, "speakable_reply": "안녕하세요! 반가워요."}
            
            if any(w in low for w in ["귀여워", "이쁘다", "예쁘다", "똑똑해", "멋져", "잘했어", "천재", "최고야"]):
                return {"intent": "shy", "normalized_text": text, "speakable_reply": "에헤헤, 부끄러워요."}
            
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
        if any(w in low_text for w in ["신나", "재밌", "좋아", "행복", "최고", "안녕", "반가", "환영", "어서오"]): self.emotion_queue.put("HAPPY")
        elif any(w in low_text for w in ["놀라운", "놀랐", "깜짝", "세상에"]): self.emotion_queue.put("SURPRISED")
        elif any(w in low_text for w in ["슬퍼", "우울", "힘들", "속상"]): self.emotion_queue.put("SAD")
        elif any(w in low_text for w in ["화나", "짜증", "싫어", "최악"]): self.emotion_queue.put("ANGRY")
        elif any(w in low_text for w in ["사랑", "다정", "따뜻", "고마워", "부끄","감사"]): self.emotion_queue.put("TENDER")
        elif any(w in low_text for w in ["궁금", "생각", "글쎄", "흠.."]): self.emotion_queue.put("THINKING")
        else: self.emotion_queue.put("NEUTRAL")

    @keep_awake
    def _transcribe_then_chat(self, wav_bytes: bytes):
        self.raise_busy_signal()
        ts = datetime.now().strftime("%H:%M:%S")

        intent = "chat"
        user_text = ""
        speak_text_full = ""

        try:
            b64 = base64.b64encode(wav_bytes).decode("ascii")
            current_face_name = self.shared_state.get('current_user_name')

            # 👉 [추가] 모티가 지금 누구랑 대화 중인지 변수로 빼냅니다.
            is_waiting_for_name = (self.last_logged_in_user == "Wait_For_Name")
            known_name = self.last_logged_in_user if self.last_logged_in_user not in [None, "Unknown", "Wait_For_Name"] else current_face_name

            # 👉 [핵심] 알고 있는 상태, 모르는 상태, 이름 묻는 상태를 프롬프트로 완벽히 분리!
            if is_waiting_for_name:
                situation_hint = (
                    "\n[시스템 힌트]: 모티가 방금 '성함이 어떻게 되시나요?'라고 질문하고 대답을 기다리는 중입니다. "
                    "사용자가 이름을 말하면 'introduction'으로 분류하고 [NAME]에 이름을 추출하세요. "
                    "🚨 단, 사용자가 질문을 무시하고 딴소리(예: '지금 몇 시야?', '춤춰')를 하면 억지로 이름으로 간주하지 말고 실제 의도로 정확히 분류하세요. "
                    "그리고 의도가 'chat'인 경우, 사용자의 말에 친절하게 대답을 다 한 뒤 맨 마지막에 반드시 '그나저나 성함이 어떻게 되시나요?'처럼 이름을 다시 묻는 질문을 자연스럽게 덧붙이세요!"
                )
            elif known_name and known_name not in ["Unknown", "Thinking..."]:
                situation_hint = (
                    f"\n[시스템 힌트]: 모티는 이미 사용자가 '{known_name}'님이라는 것을 완벽히 인지하고 대화 중입니다! "
                    f"만약 사용자가 '{known_name}'이라고 다시 자기소개를 하거나 이름을 언급하면, 절대 'introduction'으로 분류하지 말고 "
                    f"'chat'으로 분류한 뒤 '당연히 기억하고 있죠!', '알고 있어요 {known_name}님!'처럼 사람같이 능청스럽게 대답하세요. "
                    "단, 완전히 다른 새로운 이름으로 자기를 소개할 때만 'introduction'으로 분류하세요."
                )
            else:
                situation_hint = (
                    "\n[시스템 힌트]: 일반적인 대화 중입니다. 사용자가 명확하게 새롭게 자기소개를 하려는 "
                    "의도(예: '내 이름은 ~야')를 보인 게 아니라면 절대 'introduction'으로 분류하지 말고 'chat'으로 두세요."
                )

            current_time_str = datetime.now().strftime("%Y년 %m월 %d일 %p %I시 %M분").replace("AM", "오전").replace("PM", "오후")

            prompt = (
                f"현재 시간: {current_time_str}\n"
                f"현재 카메라 앞 사용자: {current_face_name}{situation_hint}\n"
                "첨부된 오디오를 듣고 다음 태그 규칙을 반드시 지켜서 순서대로 출력해.\n"
                "1. [INTENT]의도[/INTENT] (목록: 'greeting', 'shy', 'hug', 'comfort', 'introduction', 'ox_quiz', 'game', 'joke', 'dance', 'stop', 'chat' 중 택 1)\n"
                "2. [NAME]이름[/NAME] (의도가 'introduction'일 때만 사용자의 이름 2~4글자 추출. 그 외엔 생략)\n"
                "3. [USER]사용자가 한 말[/USER]\n"
                "4. 그 다음 줄부터: 모티의 다정한 대답 (동작 기능일 땐 생략)\n"
                "절대 마크다운(```)이나 다른 설명을 덧붙이지 마."
            )

            contents = list(self.chat.history)
            contents.append({
                "role": "user",
                "parts": [prompt, {"inline_data": {"mime_type": "audio/wav", "data": b64}}]
            })

            print(f"[{ts}] [Gemini] 🔥 초고속 원샷 스트리밍 호출 시작...")
            
            response_stream = self.chat.model.generate_content(contents, stream=True)

            buffer = ""
            terminators = ['.', '!', '?', '\n']
            header_parsed = False

            for chunk in response_stream:
                if not chunk.text: continue
                buffer += chunk.text

                if not header_parsed:
                    if "[/USER]" in buffer:
                        # 👉 [보너스] re.DOTALL 옵션으로 LLM의 줄바꿈 변덕 완벽 방어
                        intent_match = re.search(r'\[INTENT\](.*?)\[/INTENT\]', buffer, re.DOTALL)
                        user_match = re.search(r'\[USER\](.*?)\[/USER\]', buffer, re.DOTALL)
                        name_match = re.search(r'\[NAME\](.*?)\[/NAME\]', buffer, re.DOTALL)
                        
                        if intent_match: intent = intent_match.group(1).strip()
                        if user_match: user_text = user_match.group(1).strip()
                        extracted_name = name_match.group(1).strip() if name_match else None
                        
                        ts_receive = datetime.now().strftime("%H:%M:%S")
                        print(f"[{ts}] [User] {user_text}")
                        print(f"[{ts}] [Intent] {intent}")
                        if extracted_name: print(f"[{ts}] [Name] {extracted_name}")
                        
                        self._analyze_and_send_emotion(user_text)

                        # 👉 [핵심 2] 행동(게임, 춤 등)을 요구했을 때 파이썬 코드로 애교 부리기
                        if intent in ["dance", "stop", "game", "ox_quiz", "joke"]:
                            
                            if intent == "dance":
                                self._speak_and_subtitle("네! 신나게 춤춰볼게요!")
                                if callable(self.start_dance_cb): self.start_dance_cb()
                            elif intent == "stop":
                                if callable(self.stop_dance_cb): self.stop_dance_cb()
                            elif intent == "game": 
                                self._speak_and_subtitle("좋아요, 게임을 시작할게요!")
                                self.entertain_handler.run_rps_game()
                            elif intent == "ox_quiz": 
                                self._speak_and_subtitle("네, 퀴즈 내드릴게요!")
                                self.entertain_handler.run_ox_quiz()
                            elif intent == "joke": 
                                self._speak_and_subtitle("재미있는 얘기 해드릴게요!")
                                self.entertain_handler.run_joke()
                            
                            # 👉 행동(게임/퀴즈/농담 등)이 끝난 직후에 본론으로 돌아옵니다!
                            if is_waiting_for_name and intent != "stop":

                                self.raise_busy_signal()
                                def follow_up_after_action():
                                    try:# 동작 종류에 따라 대기 시간(초)을 다르게 설정합니다.
                                        if intent == "dance":
                                            time.sleep(42)

                                        self.last_activity_time = time.time()

                                        if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                                        self._speak_and_subtitle("그나저나, 제가 아직 성함을 못 들었어요. 이름이 어떻게 되시나요?")

                                    finally:
                                        # 🚨 [핵심 3] 질문이 끝나면 "바쁨" 깃발을 뽑아서, 대답을 들을 수 있게 합니다.
                                        self.lower_busy_signal()

                                threading.Thread(target=follow_up_after_action, daemon=True).start()
                            break

                        elif intent == "comfort":
                            proposal_text = "저런, 많이 힘드셨군요... 제가 안아드려도 될까요?"
                            self._speak_and_subtitle(proposal_text)
                            if self._quick_listen_for_yes_no(timeout=4.0):
                                speak_text_full = "네, 이리 오세요. 토닥토닥..."
                                if self.emotion_queue: self.emotion_queue.put("TENDER")
                                if callable(self.play_hug_cb): threading.Thread(target=self.play_hug_cb, daemon=True).start()
                                self._speak_and_subtitle(speak_text_full)
                            else:
                                self._speak_and_subtitle("그렇군요. 항상 옆에서 응원하고 있다는 걸 잊지 마세요. 힘내세요!")
                            
                            # 👉 [추가] 위로가 끝난 뒤 다시 본론으로 돌아오기
                            if is_waiting_for_name:
                                self._speak_and_subtitle("그나저나, 아직 성함을 못 들었는데 어떻게 되시나요?")
                            break

                        elif intent == "introduction":
                            # 👉 [수정 3] Gemini가 똑똑하게 추출한 이름을 최우선으로 사용!
                            name = extracted_name if extracted_name else user_text.split(" ")[0]
                            
                            # (카메라가 이미 알고 있는 사람이면 그 이름 유지)
                            if current_face_name and current_face_name not in ["Unknown", "Thinking..."]:
                                name = current_face_name
                                
                            print(f"💡 이름 확보 완료: '{name}'. 얼굴 학습 시작.")
                            self.profile_manager.load_profile_for_chat(name)
                            self.shared_state['current_user_name'] = name
                            self.last_logged_in_user = name
                            
                            self._speak_and_subtitle(f"반가워요 {name}님! 더 잘 기억하기 위해 얼굴을 인식할게요. 10초 동안 카메라를 봐주세요.")
                            if self.emotion_queue: self.emotion_queue.put("SCANNING")
                            
                            print("⏳ 10초 얼굴 학습 시작...")
                            self.shared_state['force_learning'] = True
                            self.shared_state['learning_target_name'] = name
                            time.sleep(10)
                            self.shared_state['force_learning'] = False
                            
                            if self.emotion_queue: self.emotion_queue.put("HAPPY")
                            speak_text_full = "등록이 완료되었습니다! 이제 대화를 시작해요."
                            self._speak_and_subtitle(speak_text_full)
                            try: self.profile_manager.save_profile_at_exit()
                            except Exception as e: print(f"❌ 프로필 저장 실패: {e}")
                            break

                        if intent == "hug" and callable(self.play_hug_cb): threading.Thread(target=self.play_hug_cb, daemon=True).start()
                        elif intent == "shy" and callable(self.play_shy_cb): threading.Thread(target=self.play_shy_cb, daemon=True).start()
                        elif intent == "greeting" and callable(self.play_greeting_cb): threading.Thread(target=self.play_greeting_cb, daemon=True).start()

                        # 헤더 파싱 후 대답 스트리밍 돌입
                        buffer = buffer.split("[/USER]")[-1].lstrip()
                        header_parsed = True
                    else:
                        continue 

                if header_parsed:
                    while any(t in buffer for t in terminators):
                        first_term_idx = min([buffer.find(t) for t in terminators if t in buffer])
                        sentence = buffer[:first_term_idx+1].strip()
                        buffer = buffer[first_term_idx+1:]
                        sentence = sentence.replace('*', '').strip()
                        
                        if sentence:
                            print(f"[{ts}] 🗣️ 말하기: {sentence}")
                            self.tts.speak(sentence)
                            speak_text_full += sentence + " "
            
            if header_parsed and buffer.strip():
                sentence = buffer.replace('*', '').strip()
                if sentence:
                    print(f"[{ts}] 🗣️ 마지막 말하기: {sentence}")
                    if self.subtitle_queue: self.subtitle_queue.put(sentence)
                    self.tts.speak(sentence)
                    speak_text_full += sentence + " "

            speak_text_full = speak_text_full.strip()

            if user_text and speak_text_full:
                # 👉 [핵심 수정] 안전하게 기억을 주입하기 위해 Chat 세션을 깨끗하게 재구축합니다.
                new_history = list(self.chat.history)
                new_history.append({'role': 'user', 'parts': [user_text]})
                new_history.append({'role': 'model', 'parts': [speak_text_full]})
                self.chat = self.chat.model.start_chat(history=new_history)

        except Exception as e:
            print(f"❌ 처리 실패: {e}\n")
            if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
            
        finally:
            print("... TTS 대기 ...")
            self.tts.wait()

            if self.emotion_queue and intent not in ["comfort", "hug", "shy"]:
                self.emotion_queue.put("NEUTRAL")

            if user_text and speak_text_full:
                log_entry = f"User: {user_text} | Moti: {speak_text_full}"
                self.session_history.append(log_entry)
                print(f"📝 대화 메모리 기록 (현재 {len(self.session_history)}턴 쌓임)")

            self.lower_busy_signal()

    def _flush_session_history(self):
        """쌓인 대화 내용을 한 번에 저장하고 버퍼를 비웁니다."""
        if not self.session_history:
            self.chat = genai.GenerativeModel(self.MODEL_NAME, system_instruction=SYSTEM_INSTRUCTION).start_chat(history=[])
            return

        print("💾 대화 세션 종료/전환. 기억을 정리하여 저장합니다...")
        
        full_conversation_log = "\n".join(self.session_history)
        
        if hasattr(self.profile_manager, "batch_update_summary"):
             threading.Thread(
                target=self.profile_manager.batch_update_summary, 
                args=(full_conversation_log,),
                daemon=True
            ).start()
        else:
             print("⚠️ ProfileManager에 batch_update_summary 메서드가 없습니다. (임시 Skip)")

        self.session_history = []

        self.chat = genai.GenerativeModel(self.MODEL_NAME, system_instruction=SYSTEM_INSTRUCTION).start_chat(history=[])
        print("🧹 Gemini 단기 기억 초기화 완료 (다음 응답 속도 최적화)")
    
    def _quick_listen_for_yes_no(self, timeout=3.0) -> bool:
        """
        3초간 음성을 듣고 '네(긍정)'인지 '아니오(부정)'인지 판단합니다.
        반환값: True(네/긍정/학습진행), False(아니오/부정/학습스킵)
        """
        print(f"👂 [Yes/No] {timeout}초간 답변 듣기 시작...")
        if self.emotion_queue: self.emotion_queue.put("LISTENING")
        
        # 1. 짧은 녹음
        try:
            recording = sd.rec(int(timeout * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE, blocking=True)
            print("✅ [Yes/No] 녹음 완료, 분석 중...")
            if self.emotion_queue: self.emotion_queue.put("THINKING")
        except Exception as e:
            print(f"❌ 녹음 실패: {e}")
            return False # 에러 시 스킵

        # 2. Gemini에게 판단 요청
        try:
            wav_bytes = self._to_wav_bytes(recording, SAMPLE_RATE, CHANNELS, DTYPE)
            b64 = base64.b64encode(wav_bytes).decode("ascii")
            
            prompt = (
                "사용자의 오디오를 듣고 '긍정(Yes)'인지 '부정(No)'인지 판단하세요. "
                "사용자가 '네', '응', '좋아', '그래', '어'라고 하면 긍정입니다. "
                "사용자가 '아니', '아니요', '됐어', '싫어'라고 하거나 아무 말도 없으면 부정입니다. "
                "반드시 JSON으로만 출력하세요: {\"answer\": \"yes\"} 또는 {\"answer\": \"no\"}"
            )
            
            resp = self.model.generate_content([
                prompt,
                {"inline_data": {"mime_type": "audio/wav", "data": b64}}
            ])
            
            txt = _extract_text(resp).lower()
            if '"yes"' in txt or "'yes'" in txt:
                print("💡 판단 결과: YES (학습 진행)")
                return True
            else:
                print("💡 판단 결과: NO (학습 스킵)")
                return False
        except Exception as e:
            print(f"❌ 판단 오류: {e}")
            return False # 안전하게 스킵

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
        
        initial_session_active = True 
        is_first_login = False 

        # [1단계] 초기 대기 루프
        while not self.stop_event.is_set() and initial_session_active:
            if self.shared_state:
                raw_name = self.shared_state.get('detected_user')
                
                # 1. 무언가 감지됨
                if raw_name and raw_name not in ["Thinking...", None]:
                    
                    # 👉 [수정] 모티의 인내심(Debounce) 로직: Unknown이더라도 진짜 이름이 뜰 때까지 최대 2.5초 기다림!
                    print(f"👀 1차 감지: '{raw_name}'. 식별 안정화 대기 중...")
                    
                    stabilize_timeout = 2.5  # 최대 기다리는 시간 (2.5초)
                    elapsed = 0.0
                    check_interval = 0.1
                    final_name = raw_name
                    
                    while elapsed < stabilize_timeout:
                        if self.stop_event.is_set(): break
                        
                        current_name = self.shared_state.get('detected_user')
                        
                        # 💡 [핵심] 기다리는 도중에 '진짜 이름'을 찾았다면, 더 안 기다리고 즉시 판단 완료!
                        if current_name and current_name not in ["Unknown", "Thinking...", None]:
                            final_name = current_name
                            break
                            
                        # 얼굴이 카메라 밖으로 완전히 사라졌다면 취소
                        if current_name is None:
                            final_name = None
                            break
                            
                        time.sleep(check_interval)
                        elapsed += check_interval
                    
                    print(f"👀 2차(최종) 감지 결과: '{final_name}'")

                    if not final_name or final_name in ["Thinking...", None]:
                        print("❌ 대기 중 얼굴을 놓쳤거나 여전히 인식 중입니다.")
                        continue 
                    
                    detected_name = final_name

                    # 로그인 상태가 바뀌었거나, Unknown인데 아직 이름을 안 물어본 경우
                    if detected_name != self.last_logged_in_user:
                        
                        # [A] Unknown 사용자: 이름을 먼저 물어봄 (학습 X)
                        if detected_name == "Unknown":
                             if self.last_logged_in_user == "Wait_For_Name":
                                 # 이미 물어보고 대답 기다리는 중이면 패스
                                 pass
                             else:
                                 print("🤖 최종 Unknown 확정 -> 이름 질문 프로세스")
                                 self.raise_busy_signal()
                                 
                                 self._speak_and_subtitle("안녕하세요! 처음 뵙네요. 성함이 어떻게 되시나요?")
                                 self.tts.wait()
                                 
                                 # 질문했음을 표시 (중복 질문 방지)
                                 self.last_logged_in_user = "Wait_For_Name"
                                 self.lower_busy_signal()

                        # [B] Known 사용자 (이미 아는 사람): 학습 여부 질문
                        else:
                            # 만약 방금 막 학습을 마친 상태(Wait_For_Name -> 실명)라면 인사 건너뛰기
                            if self.last_logged_in_user == "Wait_For_Name":
                                 # 방금 통성명하고 학습까지 마쳤으므로 루프 상의 인사는 생략하고
                                 # 현재 상태만 동기화합니다.
                                 self.last_logged_in_user = detected_name
                            else:
                                print(f"🤖 아는 사람({detected_name}) -> 학습 질문")
                                self.raise_busy_signal()
                                
                                self.profile_manager.load_profile_for_chat(detected_name)
                                self.last_logged_in_user = detected_name
                                self.shared_state['current_user_name'] = detected_name
                                
                                if self.emotion_queue: self.emotion_queue.put("HAPPY")
                                
                                # 1. 인사 및 질문
                                greeting_msg = f"{detected_name}님 안녕하세요!  {detected_name}님을 더 잘 기억할 수 있게 얼굴 인식을 수행할까요?"
                                self._speak_and_subtitle(greeting_msg)
                                self.tts.wait()

                                # 2. 답변 대기 (4초)
                                do_learning = self._quick_listen_for_yes_no(timeout=4.0)

                                if do_learning:
                                    # [YES] 재학습 수행
                                    
                                    # 1️⃣ 말부터 끝까지 확실하게 마칩니다. (이 동안은 기본 표정 유지)
                                    self._speak_and_subtitle("네! 10초 동안 카메라를 봐주세요.")
                                    
                                    # 2️⃣ 말이 완전히 끝나면 표정을 스캔 모드로 바꾸고 10초 카운트를 시작합니다!
                                    if self.emotion_queue: self.emotion_queue.put("SCANNING")
                                    print("⏳ 10초 얼굴 학습 시작...")
                                    
                                    self.shared_state['force_learning'] = True
                                    self.shared_state['learning_target_name'] = detected_name
                                    time.sleep(10) 
                                    self.shared_state['force_learning'] = False
                                    
                                    if self.emotion_queue: self.emotion_queue.put("HAPPY")
                                    self._speak_and_subtitle("얼굴 데이터 업데이트 완료! 이제 대화를 시작해요!")
                                else:
                                    # [NO] 학습 스킵
                                    if self.emotion_queue: self.emotion_queue.put("HAPPY")
                                    self._speak_and_subtitle("네, 바로 대화를 시작할게요.")
                                    self.tts.wait()
                                
                                self.lower_busy_signal()

                        # --- 공통 종료 처리 (Unknown일 때는 대화 세션만 열어둠) ---
                        self.listening_enabled.set() 
                        self.last_activity_time = time.time() 
                        
                        is_first_login = True 
                        initial_session_active = False 
                        break 
            
            if time.time() - self.last_activity_time >= 40:
                initial_session_active = False 
                
            time.sleep(0.1)

        # [2단계] 로그인 후 대화 유지 또는 SLEEPY 전환

        if is_first_login or initial_session_active:
            print("▶ 대화 세션을 유지합니다. (40초 후 비활성화)")
            
            if is_first_login:
                while not self.stop_event.is_set() and ((self.busy_signals > 0) or (time.time() - self.last_activity_time < 40)):
                    time.sleep(0.1)
                
                initial_session_active = False
            
        # [3단계] SLEEPY 전환 (2단계 로직 후 또는 1단계에서 40초 시간 초과 시)

        if not self.stop_event.is_set():
            print("▶ 대화 세션 시간 초과. 이제 핫워드 대기 상태로 전환합니다.")
            
            # 🚨 더 이상 대답하지 않도록 입 모양(Mouth) 감지 스위치를 확실하게 내립니다!
            self.listening_enabled.clear() 
            
            self._flush_session_history()

            if self.emotion_queue:
                self.emotion_queue.put("SLEEPY")

        if not self.stop_event.is_set():
            print("\n💤 모티가 잠들었습니다. '안녕 모티' 호출을 기다립니다... (종료: ESC)\n")       

        while not self.stop_event.is_set():
            if self.shared_state:
                detected_name = self.shared_state.get('detected_user')
                if detected_name and detected_name not in ["Unknown", "Thinking...", None]:
                    if detected_name != self.last_logged_in_user:
                        pass

            time.sleep(0.1)

            try:
                signal = self.hotword_queue.get(timeout=1.0)
                
                if signal == "hotword_detected" and not self.stop_event.is_set():
                    print("💡 핫워드 감지! 대화 세션을 시작합니다.")
                    self.listening_enabled.set()
                    
                    if self.last_logged_in_user and self.last_logged_in_user not in ["Unknown", "Wait_For_Name", "Thinking..."]:
                        print(f"🧠 {self.last_logged_in_user}님의 장기 기억을 뇌에 다시 불러옵니다...")
                        self.profile_manager.load_profile_for_chat(self.last_logged_in_user)

                    if self.emotion_queue: self.emotion_queue.put("WAKE")
                    self._speak_and_subtitle("네, 말씀하세요.")
                    
                    self.last_activity_time = time.time()
                    
                    while (self.busy_signals > 0) or (time.time() - self.last_activity_time < 40):
                        if self.stop_event.is_set(): break
                        time.sleep(0.1)

                    if not self.stop_event.is_set():
                        print("▶ 대화 세션 시간 초과. 다시 핫워드 대기 상태로 전환합니다.")
                        self._flush_session_history()
                        
                        self.listening_enabled.clear()
                        if self.emotion_queue:
                            self.emotion_queue.put("SLEEPY")

                        print("\n💤 모티가 잠들었습니다. '안녕 모티' 호출을 기다립니다... (종료: ESC)\n")
                            
            except queue.Empty:
                continue
            except (KeyboardInterrupt, SystemExit):
                self.stop_event.set()
                break
        
        print("PTT App 종료 절차 시작...")
        
        self._flush_session_history()
        
        self.listening_enabled.clear()
        if self.current_listener and self.current_listener.is_alive():
            self.current_listener.stop()
        
        if self.mouth_listener_thread and self.mouth_listener_thread.is_alive():
            self.mouth_listener_thread.join(timeout=1.0)
        
        try:
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