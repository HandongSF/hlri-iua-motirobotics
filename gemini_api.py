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
MODEL_NAME = _get_env("MODEL_NAME", "gemini-3-flash-preview")

ONE_SHOT_PROMPT = (
    "이 오디오를 전사하고 의도를 분류하며, 'chat', 'greeting', 'shy', 'introduction' 의도에 대해서만 1~2문장의 따뜻한 답변을 작성하세요. "
    "사용자가 '안녕', '반가워' 등 인사를 하면 의도를 'greeting'으로 분류하세요.\n"
    "사용자가 '귀여워', '똑똑해', '멋져', '최고야' 등 칭찬을 하면 의도를 'shy'로 분류하세요.\n"
    "introduction 의도인 경우 이름을 추출하세요. (다른 의도는 reply를 빈 문자열로, name은 null)\n"
    "오디오 컨텍스트에 인식된 이름이 있고, 사용자가 자신의 이름을 물으면 그 이름을 사용해 답변하세요.\n"
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
                 play_shy_cb: Optional[Callable[[], None]] = None,
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
        import re

        if not text_data:
            return

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
                if text_to_process.strip():
                    sentences = [text_to_process.strip()]
                else:
                    return

            for sentence in sentences:
                if self.subtitle_queue:
                    self.subtitle_queue.put(sentence)
                
                self.tts.speak(sentence)
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
        model_text = ""
        speak_text = ""

        try:
            b64 = base64.b64encode(wav_bytes).decode("ascii")
            current_face_name = self.shared_state.get('current_user_name')
            name_context = f" (카메라를 통해 현재 인식된 사용자 이름: {current_face_name})" if current_face_name else ""

            full_prompt = ONE_SHOT_PROMPT + name_context

            content_payload = [
                full_prompt,
                {"inline_data": {"mime_type": "audio/wav", "data": b64}}
            ]

            print(f"[{ts}] [Gemini] 오디오 전송 및 처리 중...")
            response = self.chat.send_message(content_payload)
            
            json_text = _extract_text(response)
            json_text = re.sub(r"```json\s*", "", json_text)
            json_text = re.sub(r"```", "", json_text).strip()
            
            try:
                result = json.loads(json_text)
            except json.JSONDecodeError:
                print(f"⚠️ JSON 파싱 실패. Raw response: {json_text}")
                result = {"text": "(음성 인식)", "intent": "chat", "reply": json_text, "name": None}

            user_text = result.get("text", "")
            intent = result.get("intent", "chat")
            speak_text = result.get("reply", "")
            name = result.get("name")

            current_face_name = self.shared_state.get('current_user_name')

            if current_face_name and current_face_name not in ["Unknown", "Thinking...", None]:
                low_user_text = user_text.lower()
                if any(k in low_user_text for k in ["내 이름", "제가 누구", "저 누구"]):
                    speak_text = f"당신은 {current_face_name}님이시군요! 이제 제 기억 속에도 확실히 저장되었어요."
                    intent = "chat" 
                    print(f"💡 이름 질문 감지, 답변을 '{current_face_name}'으로 덮어씀.")
                    pass

            print(f"[{ts}] [User] {user_text}")
            print(f"[{ts}] [Intent] {intent}")
            
            if intent == "introduction":
                target_name = None
                if current_face_name and current_face_name not in ["Unknown", "Thinking...", None]:
                      target_name = current_face_name
                      print(f"💡 자기소개 감지. 카메라 인식 이름 '{target_name}' 우선 사용.")
                elif name:
                      target_name = name
                      print(f"💡 자기소개 감지. Gemini 추출 이름 '{target_name}' 사용.")
                
                if target_name: 
                    print(f"💡 이름 확보 완료: '{target_name}'. 얼굴 학습 시작.")
                    
                    self.profile_manager.load_profile_for_chat(target_name)
                    self.shared_state['current_user_name'] = target_name
                    self.last_logged_in_user = target_name
                    
                    if self.emotion_queue:
                        self.emotion_queue.put("HAPPY")

                    full_greeting_and_guide = (speak_text + " " + "더 잘 기억하기 위해 얼굴을 인식할게요. 10초 동안 카메라를 보시고 얼굴을 위 아래 좌 우로 움직여주세요. 다양한 표정도 좋아요.")
                    self._speak_and_subtitle(full_greeting_and_guide)

                    self.tts.wait()
                    
                    if self.emotion_queue:
                        self.emotion_queue.put("SCANNING")

                    self.shared_state['force_learning'] = True
                    self.shared_state['learning_target_name'] = target_name
                    
                    print("⏳ 10초 얼굴 학습 시작...")
                    time.sleep(10)
                    
                    self.shared_state['force_learning'] = False

                    if self.emotion_queue:
                        self.emotion_queue.put("NEUTRAL")

                    self._speak_and_subtitle("등록이 완료되었습니다! 무엇을 도와드릴까요?")
                    
                    speak_text = ""
                else:
                    print("⚠️ 'introduction' 의도는 감지되었으나, 유효한 이름이 추출되지 않았습니다. 학습을 건너뜁니다.")

            if intent == "greeting":
                print("💡 의도: GREETING (인사)")
                if callable(self.play_greeting_cb):
                    threading.Thread(target=self.play_greeting_cb, daemon=True).start()
                if self.emotion_queue: 
                    self.emotion_queue.put("HAPPY")
                if not speak_text:
                    speak_text = "안녕하세요! 만나서 반가워요."

            elif intent == "shy":
                print("💡 의도: SHY (부끄부끄)")
                
                if callable(self.play_shy_cb):
                    threading.Thread(target=self.play_shy_cb, daemon=True).start()
                
                if self.emotion_queue:
                    self.emotion_queue.put("TENDER")
                
                if not speak_text:
                    speak_text = "에헤헤, 부끄러워요. 감사합니다!"

            elif intent == "dance":
                print("💡 의도: DANCE START")
                self._speak_and_subtitle("네! 신나게 춤춰볼게요!")
                speak_text = ""

                dance_time = 0
                if callable(self.start_dance_cb): 
                    result = self.start_dance_cb()
                    print(f"🕵️ [DEBUG] 춤 함수 반환값: {result}")

                    if isinstance(result, (int, float)) and result > 0:
                        dance_time = result
                    else:
                        print("⚠️ 춤 시간이 확인되지 않아 기본값(40초)으로 설정합니다.")
                        dance_time = 40

                if self.emotion_queue: 
                    self.emotion_queue.put("EXCITED")
                
                print(f"⏳ 춤추는 중... ({dance_time}초간 음성인식 차단)")
                time.sleep(dance_time)
                print("✅ 춤 종료 대기 끝, 다시 듣기 모드 전환")

            elif intent == "stop":
                print("💡 의도: STOP")
                if callable(self.stop_dance_cb): self.stop_dance_cb()
                if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                speak_text = "" 

            elif intent == "game":
                print("💡 의도: GAME")
                self.entertain_handler.run_rps_game()
                speak_text = ""

            elif intent == "ox_quiz":
                print("💡 의도: OX QUIZ")
                self.entertain_handler.run_ox_quiz()
                speak_text = ""

            elif intent == "joke":
                print("💡 의도: JOKE")
                self.entertain_handler.run_joke()
                speak_text = ""
            
            if speak_text:
                print(f"[{ts}] [Gemini Reply] {speak_text}")

                self._analyze_and_send_emotion(speak_text)

                self._speak_and_subtitle(speak_text)
                model_text = speak_text
            
            if self.emotion_queue and intent == "chat":
                self.emotion_queue.put("NEUTRAL")

        except Exception as e:
            print(f"❌ 처리 실패: {e}\n")
            if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
        
        finally:
            print("... TTS 대기 ...")
            self.tts.wait()

            if (intent == "chat" or intent == "introduction" or intent == "greeting" or intent == "shy") and user_text and model_text:
                log_entry = f"User: {user_text} | Moti: {model_text}"
                self.session_history.append(log_entry)
                print(f"📝 대화 메모리 기록 (현재 {len(self.session_history)}턴 쌓임)")

            self.lower_busy_signal()

    def _flush_session_history(self):
        """쌓인 대화 내용을 한 번에 저장하고 버퍼를 비웁니다."""
        if not self.session_history:
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
    
    # ▼▼▼ [NEW] 짧게 듣고 네/아니오 판단하는 함수 ▼▼▼
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
                
                # 1. 무언가 감지됨 (Thinking이나 None이 아님)
                if raw_name and raw_name not in ["Thinking...", None]:
                    
                    # ▼▼▼ [안전장치 추가] 인식 안정화 대기 (1.5초) ▼▼▼
                    # 이유: 인식 초기에는 'Unknown'이었다가 잠시 후 '홍길동'으로 바뀔 수 있으므로
                    # 즉시 판단하지 않고 잠시 기다립니다.
                    print(f"👀 얼굴 감지됨('{raw_name}')... 인식이 확실해질 때까지 1.5초 대기합니다.")
                    time.sleep(1.5) 
                    
                    # 1.5초 후 최종 이름 다시 확인
                    final_name = self.shared_state.get('detected_user')
                    if not final_name or final_name in ["Thinking...", None]:
                        print("👀 얼굴이 사라졌거나 다시 탐색 중입니다. 대기를 계속합니다.")
                        continue # 다시 루프 처음으로
                    
                    detected_name = final_name
                    print(f"✅ 최종 인식 결과: {detected_name}")
                    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

                    if detected_name != self.last_logged_in_user:
                        print(f"👀 로그인 프로세스 시작: {detected_name}")
                        self.raise_busy_signal()
                        
                        # [A] Unknown 사용자: 기존 로직 (무조건 학습)
                        if detected_name == "Unknown":
                            print("🤖 Unknown 확정 -> 무조건 학습 시작")
                            self._speak_and_subtitle("안녕하세요! 처음 뵙네요. 얼굴을 익히기 위해 10초만 학습할게요.")
                            self.tts.wait()
                            
                            if self.emotion_queue: self.emotion_queue.put("SCANNING")
                            self.shared_state['force_learning'] = True
                            self.shared_state['learning_target_name'] = "NewUser" 
                            time.sleep(10)
                            self.shared_state['force_learning'] = False
                            
                            if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                            self._speak_and_subtitle("학습이 완료되었습니다! 대화를 시작할게요.")

                        # [B] Known 사용자 (이미 아는 사람)
                        else:
                            print(f"🤖 아는 사람({detected_name}) 확정 -> 학습 여부 물어보기")
                            self.profile_manager.load_profile_for_chat(detected_name)
                            self.last_logged_in_user = detected_name
                            self.shared_state['current_user_name'] = detected_name
                            
                            if self.emotion_queue: self.emotion_queue.put("HAPPY")
                            
                            # 1. 인사 및 질문
                            greeting_msg = f"{detected_name}님 안녕하세요! 더 잘 기억할 수 있게 얼굴 인식을 수행할까요?"
                            self._speak_and_subtitle(greeting_msg)
                            self.tts.wait()

                            # 2. 답변 대기 (3초)
                            # 질문이 끝나자마자 대답을 듣기 위해 타임아웃을 넉넉히(4초) 줍니다.
                            do_learning = self._quick_listen_for_yes_no(timeout=4.0)

                            if do_learning:
                                # [YES] 학습 수행
                                if self.emotion_queue: self.emotion_queue.put("SCANNING")
                                self._speak_and_subtitle(f"{detected_name}님을 더 잘 기억하기 위해 얼굴을 인식할게요. 10초 동안 카메라를 보시고 얼굴을 위 아래 좌 우로 움직여주세요. 다양한 표정도 좋아요.")
                                self.tts.wait()

                                self.shared_state['force_learning'] = True
                                self.shared_state['learning_target_name'] = detected_name
                                time.sleep(10) 
                                self.shared_state['force_learning'] = False
                                if self.emotion_queue: 
                                    self.emotion_queue.put("HAPPY")
                                self._speak_and_subtitle("얼굴 데이터 업데이트 완료! 이제 대화를 시작해요!")
                            else:
                                # [NO] 학습 스킵
                                if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                                self._speak_and_subtitle("네, 바로 대화를 시작할게요.")
                                self.tts.wait()

                        # --- 공통 종료 처리 ---
                        self.listening_enabled.set() 
                        self.last_activity_time = time.time() 
                        self.lower_busy_signal() 
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

        if not self.stop_event.is_set() and not self.listening_enabled.is_set():
            if is_first_login and (time.time() - self.last_activity_time) >= 40:
                 initial_session_active = False
                 self.listening_enabled.clear() 
                 
            elif not is_first_login and not initial_session_active:
                 self.listening_enabled.clear() 


        if not self.stop_event.is_set() and not self.listening_enabled.is_set():
            print("▶ 대화 세션 시간 초과. 이제 핫워드 대기 상태로 전환합니다.")
            
            self._flush_session_history()

            if self.emotion_queue:
                self.emotion_queue.put("SLEEPY")

        while not self.stop_event.is_set():
            if self.shared_state:
                detected_name = self.shared_state.get('detected_user')
                if detected_name and detected_name not in ["Unknown", "Thinking...", None]:
                    if detected_name != self.last_logged_in_user:
                        pass

            time.sleep(0.1)

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
                        self._flush_session_history()
                        
                        self.listening_enabled.clear()
                        if self.emotion_queue:
                            self.emotion_queue.put("SLEEPY")
                            
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