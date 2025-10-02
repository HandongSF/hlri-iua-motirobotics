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
# gemini_api.py

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
from datetime import datetime
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
    "너는 공감 서비스 로봇 '모티'야. 한국어로 1~2문장, 따뜻하고 간결하게 답해."
    " 사용자의 정서 신호(피곤, 스트레스, 불안)를 반영해 공감하고,"
    " 사실이 불확실하면 짧게 확인 질문을 해. 과장·가스라이팅 금지."
)
TTS_RATE = int(_get_env("TTS_RATE", "0"))
TTS_VOLUME = int(_get_env("TTS_VOLUME", "100"))
TTS_FORCE_VOICE_ID = _get_env("TTS_FORCE_VOICE_ID", "")
TTS_OUTPUT_DEVICE = _get_env("TTS_OUTPUT_DEVICE", "")
GREETING_TEXT = _get_env("GREETING_TEXT", "안녕하세요! 모티입니다.")
FAREWELL_TEXT = _get_env("FAREWELL_TEXT", "도움이 되었길 바라요. 언제든 다시 불러주세요.")
ENABLE_GREETING = _get_env("ENABLE_GREETING", "1") not in ("0", "false", "False")

def _extract_text(resp) -> str:
    t = getattr(resp, "text", None)
    if t and str(t).strip():
        return str(t).strip()
    try:
        pieces = []
        for c in getattr(resp, "candidates", []) or []:
            content = getattr(c, "content", None)
            if not content: continue
            for p in getattr(content, "parts", []) or []:
                pt = getattr(p, "text", None)
                if pt and str(pt).strip():
                    pieces.append(str(pt).strip())
        if pieces:
            return "\n".join(pieces).strip()
    except Exception: pass
    try: return str(resp).strip()
    except Exception: return ""

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
                "dance=사용자가 실제로 춤을 '시작하라고' 명령/요청/승인. "
                "game=가위바위보 게임을 시작하자는 요청. "
                "ox_quiz=얼굴 인식 OX 퀴즈 게임을 시작하자는 요청. "
                "joke=개그나 농담을 해달라는 명확한 요청. "  # "joke" 의도 정의 추가
                "stop=춤을 '멈추라'는 명령/요청/승인. "
                "chat=일반 대화(질문/잡담/설명/감정표현/춤에 대한 견해·가정적 질문 포함). "
                "부정/금지/거절 표현(예:'춤 추지 마','춤은 안돼','그만두지 말고 계속')은 정확히 반영하라. "
                "오직 아래 JSON만 출력:\n"
                '{ "intent": "dance|stop|game|ox_quiz|chat|joke", "normalized_text": "<의미만 보존한 간결한 문장>", '
                '"speakable_reply": "<의도가 chat일 때 1~2문장 공감형 짧은 답변. dance/stop/game/joke/ox_quiz이면 빈 문자열>" }'
            ),
            generation_config={"response_mime_type": "application/json", "temperature": 0.2}
        )
        
        self.start_dance_cb = start_dance_cb
        self.stop_dance_cb  = stop_dance_cb
        self.play_rps_motion_cb = play_rps_motion_cb
        self.emotion_queue = emotion_queue
        self.subtitle_queue = subtitle_queue
        self.hotword_queue = hotword_queue
        self.stop_event = stop_event or threading.Event()
        
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

    def _speak_and_subtitle(self, text_data: str | dict):
        """TTS 출력과 자막 전송을 동시에 처리하는 헬퍼 함수"""
        if not text_data:
            return

        # 입력값이 딕셔너리일 경우 'text' 키의 값을, 문자열일 경우 그대로 사용
        text_to_display = ""
        if isinstance(text_data, dict):
            text_to_display = text_data.get("text", "")
        else:
            text_to_display = str(text_data)

        # 자막 큐가 존재하고, 표시할 텍스트가 있으면 큐에 넣음
        if self.subtitle_queue and text_to_display:
            self.subtitle_queue.put(text_to_display)
        
        # TTS로 음성 출력
        self.tts.speak(text_data)
        
    def _announcement_worker(self):
        # 안내 방송을 60초마다 반복하는 스레드 워커 함수입니다
        announcement_text = "한동의 미남 미녀 여러분 안녕하세요. 잠시만 주목해주세요! 30분부터 모티와 함께하는 즐거운 시간이 시작됩니다. 많은 관심과 참여 부탁드려요. "
        print("📢 안내 방송 스레드가 시작되었습니다.")
        try:
            while not self.stop_announcement_event.is_set():
                self._speak_and_subtitle(announcement_text)
                interrupted = self.stop_announcement_event.wait(timeout=60.0)
                if interrupted:
                    break
        finally:
            # 스레드가 어떤 이유로든 종료될 때, 뒷정리를 확실하게 합니다.
            # 이것이 경쟁 상태를 막는 핵심입니다.
            self.lower_busy_signal()
            self.announcement_active = False
            print("🛑 안내 방송 스레드가 종료되었습니다.")

    def toggle_announcement(self):
        """'p' 키에 반응해 안내 방송을 켜고 끄는 더 안정적인 함수입니다."""
        # 불안정한 상태 변수 대신, 실제 스레드가 살아있는지를 직접 확인합니다.
        is_running = self.announcement_thread is not None and self.announcement_thread.is_alive()

        if is_running:
            # 스레드가 살아있다면, 중지 신호를 보냅니다.
            print("...안내 방송 중지 신호를 보냅니다...")
            self.stop_announcement_event.set()
        else:
            # 스레드가 없거나 죽었다면, 새로 시작합니다.
            print("...안내 방송 시작을 시도합니다...")
            self.raise_busy_signal()
            self.announcement_active = True # 시작 직전에만 상태를 True로 설정
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
                result_container.extend(quizzes) # 결과를 컨테이너에 추가
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
        print("🎙️  녹음 시작 (스페이스바 유지 중)...")

    def _stop_recording_and_transcribe(self):
        if not self.state.recording: return
        print("⏹️  녹음 종료, 전사 중...")
        self.state.recording = False
        try:
            if self.state.stream: self.state.stream.stop(); self.state.stream.close()
        finally: self.state.stream = None
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
            if intent not in ("dance", "stop", "game", "chat", "joke", "ox_quiz"): intent = "chat"
            return {"intent": intent, "normalized_text": str(data.get("normalized_text", text)), "speakable_reply": str(data.get("speakable_reply", "")) if intent == "chat" else ""}
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
        try:
            b64 = base64.b64encode(wav_bytes).decode("ascii")
            parts = [{"text": PROMPT_TEXT}, {"inline_data": {"mime_type": "audio/wav", "data": b64}}]
            resp = self.model.generate_content(parts)
            user_text = _extract_text(resp)
            if not user_text: print("📝 전사 결과가 비어 있습니다.\n"); return
            ts = datetime.now().strftime("%H:%M:%S"); print(f"[{ts}] [User ] {user_text}")
            route = self._route_intent(user_text)
            intent, model_text, speak_text = route["intent"], "", ""

            if intent == "chat":
                if route.get("speakable_reply"): model_text = route["speakable_reply"]
                else: reply = self.chat.send_message(user_text); model_text = _extract_text(reply) or ""
                speak_text = model_text
                self._analyze_and_send_emotion(model_text) 

            elif intent == "dance":
                print("💡 의도: DANCE START")
                if callable(self.start_dance_cb):
                    try: 
                        self.raise_busy_signal() 
                        self.start_dance_cb()
                    except Exception as e: print(f"⚠️ start_dance_cb 실행 오류: {e}")
                
                if self.emotion_queue:
                    chosen_emotion = random.choice(["EXCITED"])
                    self.emotion_queue.put(chosen_emotion)
                    print(f"💃 춤 시작! 표정을 {chosen_emotion}로 변경합니다.")

                model_text = "네! 모티가 춤을 춰볼게요"; speak_text = "네! 모티가 춤을 춰볼게요"

            elif intent == "stop":
                print("💡 의도: DANCE STOP")
                if callable(self.stop_dance_cb):
                    try: 
                        self.stop_dance_cb()
                        self.lower_busy_signal() 
                    except Exception as e: print(f"⚠️ stop_dance_cb 실행 오류: {e}")
                
                if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                model_text = "(춤 정지 명령 처리)"

            elif intent == "joke":
                print("💡 의도: JOKE (AI 실시간 생성 방식)")
                try:
                    self.raise_busy_signal()
                    if self.emotion_queue: self.emotion_queue.put("THINKING")
                    self._speak_and_subtitle("위잉 회로 풀가동! 여러분의 모터가 빠질만한 개그를 생성하는 중입니다")
                    self.tts.wait()
                    # 1. AI에게 보내는 지시문(프롬프트) 수정
                    joke_prompt = (
                        "너는 '모티'라는 로봇이야. '로봇', '컴퓨터', '전기'와 관련된, 어린아이도 이해할 수 있는 매우 창의적인 아재개그를 딱 하나만 만들어줘. "
                        "이전에 만들었던 농담과는 다른 새로운 농담이어야 해. "
                        "중요한 규칙: '삐빅' 같은 로봇 효과음은 절대 넣지 마. "
                        "출력은 반드시 다음 JSON 형식이어야 해. 다른 설명은 절대로 추가하지 마.\n"
                        '{ "question": "<퀴즈 형식의 질문>", "answer": "<짧은 답변>", "explanation": "왜냐하면, <답변에 대한 1~2문장의 유머러스한 설명>" }'
                    )

                    joke_data = None
                    try:
                        # 2. JSON 출력을 기대하며 Gemini 모델 호출
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
                    
                    # 3. 농담이 성공적으로 생성되었을 경우 실행
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
                    self.lower_busy_signal()

            elif intent == "ox_quiz":
                print("💡 의도: OX QUIZ GAME (라운드 방식)")

                if not self.shared_state or not self.ox_command_q:
                    self._speak_and_subtitle("시스템 오류로 퀴즈를 진행할 수 없어요.")
                    print("❌ shared_state 또는 ox_command_q가 없어 모드 전환 불가")
                    return
                
                predefined_quizzes = [
                    {"question": "제 이름은 모터입니다", "answer": "X", "explanation": "제 이름은 모티, 모티예요! 꼭 기억해주세요."},
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
                        "question": "개미는 높은 곳에서 떨어지면 죽는다는 말이 틀렸다는 것을 부정하는 것은 옳지 않다.",
                        "answer": "O",
                        "explanation": "개미는 높은 곳에서 떨어져도 죽지 않아요"
                    },
                    6: { # 7번째 본 게임 라운드
                        "question": "모티의 이름은 8월 12일에 지어졌다 라는 문장에 들어간 ㅇ의 개수는 8개이다.",
                        "answer": "X",
                        "explanation": "해당 문장에서 ㅇ은 총 7개입니다."
                    }
                }

                # 백그라운드 스레드가 결과를 담을 컨테이너 생성
                quiz_result_container = []
                self.generated_quizzes = []

                quiz_round_counter = 0
                main_game_round_counter = 0
                is_first_round = True
                is_game_over = False
                is_main_game_started = False
                is_crazy_mode_active = False 

                try:
                    self.raise_busy_signal()
                    self.shared_state['mode'] = 'ox_quiz'
                    # 진행모드
                    self._speak_and_subtitle("안녕하세요 한동의 미남 미녀 여러분! 저는 따뜻한 공감이 필요한 여러분을 위해 태어난 로봇! 모티입니다. 7주차 시험 기간 다들 정말 고생 많으시죠?.. 밤새 붙잡던 전공 책, 머릿속을 맴도는 공식들... 몸도 마음도 지쳤을 여러분을 보니 저도 마음이 짠해요. 괜찮다면, 잠시만이라도 머리 식힐 겸 저와 함께 즐거운 시간을 보내는 건 어떠세요? 복잡한 건 잠시 잊고, 모티와 함께 잠시 웃어요!")    
                    self.tts.wait()
                    self._speak_and_subtitle("그럼 지금부터 여러분과 OX 퀴즈 게임을 시작하겠습니다!")

                    # 기존모드
                    # self.tts.speak("안녕하세요! 지금부터 OX 퀴즈 게임을 시작하겠습니다.")
                    self.tts.wait()
                    self._speak_and_subtitle("먼저, 몸풀기로 연습문제를 몇 개 풀어볼게요. 첫 문제 나갑니다!")
                    self.tts.wait()

                     # 백그라운드에서 퀴즈 생성을 시작시킴
                    quiz_fetch_thread = threading.Thread(
                        target=self._fetch_quizzes_in_background,
                        args=(quiz_result_container,)
                    )
                    quiz_fetch_thread.start()

                    while not is_game_over and not self.stop_event.is_set():
                        if not is_first_round and not is_predefined:
                            print("  - 🤔 다음 라운드 준비를 위해 THINKING 표정으로 변경")
                            if self.emotion_queue: self.emotion_queue.put("THINKING")
                            time.sleep(1) # 표정이 바뀔 시간 확보

                        quiz_data = None
                        is_predefined = False

                        if quiz_round_counter < len(predefined_quizzes):
                            quiz_data = predefined_quizzes[quiz_round_counter]
                            is_predefined = True 
                            print(f" - 사전 정의된 퀴즈 #{quiz_round_counter + 1} 사용: {quiz_data}")
                            quiz_round_counter += 1
                        else:
                            if not is_main_game_started: # "본 게임 시작" 안내는 한 번만 하도록 수정
                                self._speak_and_subtitle("자, 이제 연습이 끝났습니다! 지금부터 본격적으로 시작하겠습니다.")
                                self.tts.wait()

                                # 백그라운드 작업이 끝날 때까지 여기서 잠시 대기 (대부분 바로 통과)
                                print("  - ⌛ 본 게임 퀴즈 준비 완료 여부 확인 중...")
                                quiz_fetch_thread.join(timeout=5.0) # 최대 5초간 기다림

                                # 백그라운드에서 가져온 퀴즈를 self 변수로 옮김
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
                                # 크레이지 모드였는데, 일반 문제로 돌아온 경우
                                if is_crazy_mode_active:
                                    print("일반 난이도로 돌아갑니다.")
                                    if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                                    is_crazy_mode_active = False # 상태를 '일반 모드'로 다시 변경

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
                        
                            if random.random() < 0.5: # 50% 확률
                                thinking_phrases = [
                                    "음... 어떤 문제를 내볼까?",
                                    "히히 이거 재미있겠다.",
                                    "이번에는 조금 어려울 수도 있어요."
                                    "과연 맞출 수 있을까요?"
                                    "인간에겐 너무 어려웠나? 쉽게 갈까요?"
                                ]
                                # 위 리스트에서 무작위로 문장 하나를 선택하여 말합니다.
                                self._speak_and_subtitle(random.choice(thinking_phrases))
                                self.tts.wait() # 추임새를 끝까지 말하도록 기다립니다.
                        
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
                            # ✨ 2. 워커로부터 딕셔너리(Dictionary) 형태로 결과 수신
                            round_result = self.ox_result_q.get(timeout=35)
                            print(f"OX 퀴즈 라운드 결과 수신: {round_result}")

                            # 딕셔너리에서 필요한 정보 추출
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

                            # ✨ 3. 워커가 보내준 결과 메시지를 음성으로 출력
                            self._speak_and_subtitle(message_to_speak)
                            self.tts.wait()
                            
                            # ✨ 4. 'winner_count'를 이용한 새로운 게임 흐름 제어 로직
                            if is_predefined_from_worker:
                                time.sleep(2)
                                continue  # 연습문제는 항상 다음 라운드로 진행
                            
                            if winner_count > 1:
                                # --- 2. 게임이 계속될 경우 (2명 이상 생존) ---
                                print("  - 😊 정답자가 있어 HAPPY 표정으로 변경")
                                if self.emotion_queue: self.emotion_queue.put("HAPPY")
                                
                                # ✨ 표정을 보여주기 위해 3초간 기다립니다.
                                time.sleep(3)
                                continue

                            elif winner_count == 1:
                                # --- 3. 최종 우승자가 나왔을 경우 ---
                                print("  - 🎉 최종 우승! HAPPY 표정으로 변경")
                                if self.emotion_queue: self.emotion_queue.put("HAPPY")
                                time.sleep(10) # 축하 세레모니 시간
                                is_game_over = True

                            else: # winner_count == 0
                                # --- 4. 전원 탈락했을 경우 ---
                                print("  - 😥 정답자가 없어 SAD 표정으로 변경")
                                if self.emotion_queue: self.emotion_queue.put("SAD")
                                time.sleep(10) # 아쉬움을 표현할 시간
                                is_game_over = True

                        except queue.Empty:
                            print("OX 퀴즈 시간 초과. 워커로부터 결과를 받지 못했습니다.")
                            self._speak_and_subtitle("이런, 시간 안에 결과를 받지 못했어요. 게임을 종료합니다.")
                            is_game_over = True
                    
                    self.tts.wait()

                    self._speak_and_subtitle("최후의 생존자와 가위바위보 게임을 진행할게요! 만약 여기서 이기시면 어마무시한 선물을 드리도록 하겠습니다! 하지만 패배하시면 벌칙을 받게 될거에요! 마음의 준비가 되시면 가위바위보라고 말씀해주세요!")
                    
                    model_text = "OX 퀴즈 게임 종료."

                finally:
                    if self.shared_state:
                        self.shared_state['mode'] = 'tracking'
                    self.lower_busy_signal()
                    if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
                

            elif intent == "game":
                print("💡 의도: ROCK PAPER SCISSORS GAME")
                try:
                    self.raise_busy_signal() 
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

                        # ✨ 게임 결과에 따라 표정 변화 추가
                        if "아고! 실수로 눈을" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("CLOSE")
                        elif "당신이 이겼어요" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("SAD")
                        elif "제가 이겼네요" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("HAPPY")
                        elif "비겼네요" in game_result:
                            if self.emotion_queue: self.emotion_queue.put("SURPRISED")
                            
                        time.sleep(2) # 표정을 보여주기 위해 잠시 대기

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
                                starts_dance = True # 춤이 시작되었음을 표시
                            break

                        else:
                            self._speak_and_subtitle("또 하고 싶으시면 '가위바위보'라고 말해주세요.")
                            break
                finally:
                    if not starts_dance:
                        self.lower_busy_signal()
                
                    model_text = f"게임 종료. 최종 결과: {final_game_result}"
                    if self.emotion_queue: self.emotion_queue.put("NEUTRAL")
            
            print(f"[{ts}] [Gemini] {model_text}\n")
            if speak_text: 
                if self.subtitle_queue:
                    self.subtitle_queue.put(speak_text)

                self.tts.speak(speak_text)
            
        except Exception as e: print(f"❌ 처리 실패: {e}\n")

    def _speak_farewell(self):
        try:
            self.raise_busy_signal()
            print("💡 'l' 키 입력 감지. 작별 인사를 시작합니다.")
            farewell_text = (
                "여러분 지금까지 저와 함께 좋은 시간을 보내주셔서 너무 감사드려요. "
                "벌써 헤어져야 하는 시간이 됐어요. 아쉬워라! "
                "지금 보이시는 QR에 들어가셔서 설문조사 해주세요. "
                "여러분의 작은 목소리가 저에게 큰 힘이 될거에요. "
                "앞으로 더욱 씩씩하고 멋지게 성장한 모티의 모습이 보고 싶으시면 많은 관심과 사랑 부탁드려요. "
                "여러분! 시험 공부 끝까지 포기하지 말고 힘내셔서 좋은 성과 있으시길 바라요. 그럼 다음에 또 돌아올게요! "
                "지금까지 저는 여러분의 공감 서비스 로봇, 모티! 모티였습니다!"
            )
            self._speak_and_subtitle(farewell_text)
            self.tts.wait()
            print("작별 인사 완료. 1초 후 프로그램을 종료합니다.")
            time.sleep(1)
            
        finally:
            self.lower_busy_signal()
            
    def _on_press(self, key):
        if self.stop_event.is_set(): return False
        try:
            if key == keyboard.Key.space: self._start_recording()
        except Exception as e: print(f"[키 처리 오류 on_press] {e}", file=sys.stderr)

    def _on_release(self, key):
        if self.stop_event.is_set(): return False
        try:
            if key == keyboard.Key.space:
                self.last_activity_time = time.time()
                self._stop_recording_and_transcribe()

            elif key == keyboard.KeyCode.from_char('p'):
                self.toggle_announcement()

            elif key == keyboard.KeyCode.from_char('l'):
                print("💡 'l' 키 입력 감지. 작별 인사를 시작합니다.")
                threading.Thread(target=self._speak_farewell, daemon=True).start()

            elif key == keyboard.Key.esc:
                print("ESC 감지 -> 종료 신호 보냄")
                self.stop_announcement_event.set() 
                self.stop_event.set()
                
                if self.current_listener and self.current_listener.is_alive():
                    self.current_listener.stop()
                return False 
            
        except AttributeError:
        # pynput 라이브러리에서 space, esc 같은 특수 키를 처리할 때 발생할 수 있는 오류를 방지합니다.
        # 기존 로직은 그대로 유지합니다.
            if key == keyboard.Key.space:
                self.last_activity_time = time.time()
                self._stop_recording_and_transcribe()
            elif key == keyboard.Key.esc:
                print("ESC 감지 -> 모든 스레드에 종료 신호 보냄")
                self.stop_announcement_event.set() 
                self.stop_event.set()
                if self.current_listener and self.current_listener.is_alive():
                    self.current_listener.stop()
                return False    
            
        except Exception as e: print(f"[키 처리 오류 on_release] {e}", file=sys.stderr)

    def run(self):
        print("▶ 초기 대화 세션을 시작합니다. (40초 후 비활성화)")
        self.last_activity_time = time.time()
        self.current_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.current_listener.start()
        
        while not self.stop_event.is_set() and ((self.busy_signals > 0) or (time.time() - self.last_activity_time < 40)):
            time.sleep(0.1)

        if self.current_listener.is_alive():
            self.current_listener.stop()
            self.current_listener = None 

        if not self.stop_event.is_set():
            print("▶ 초기 대화 세션 시간 초과. 이제 핫워드 대기 상태로 전환합니다.")
            if self.emotion_queue:
                self.emotion_queue.put("SLEEPY")

        while not self.stop_event.is_set():
            print("▶ '안녕 모티' 호출(SLEEPY 상태에서)을 기다립니다... (종료: ESC)")
            try:
                signal = self.hotword_queue.get(timeout=1.0)
                
                if signal == "hotword_detected" and not self.stop_event.is_set():
                    print("💡 핫워드 감지! 대화 세션을 시작합니다.")
                    if self.emotion_queue: self.emotion_queue.put("WAKE")
                    self._speak_and_subtitle("네, 말씀하세요.")
                    
                    self.last_activity_time = time.time()
                    self.current_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
                    self.current_listener.start()
                    
                    while (self.busy_signals > 0) or (time.time() - self.last_activity_time < 40):
                        if self.stop_event.is_set(): break
                        time.sleep(0.1)

                    if self.current_listener.is_alive():
                        self.current_listener.stop()
                    
                    if not self.stop_event.is_set():
                            print("▶ 대화 세션 시간 초과. 다시 핫워드 대기 상태로 전환합니다.")
                            if self.emotion_queue:
                                self.emotion_queue.put("SLEEPY")
            except queue.Empty:
                continue
            except (KeyboardInterrupt, SystemExit):
                self.stop_event.set()
                break
        
        print("PTT App 종료 절차 시작...")
        if self.current_listener and self.current_listener.is_alive():
            self.current_listener.stop()
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