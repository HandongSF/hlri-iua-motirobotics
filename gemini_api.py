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
import time  # 추가
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

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
            if not content:
                continue
            for p in getattr(content, "parts", []) or []:
                pt = getattr(p, "text", None)
                if pt and str(pt).strip():
                    pieces.append(str(pt).strip())
        if pieces:
            return "\n".join(pieces).strip()
    except Exception:
        pass
    try:
        return str(resp).strip()
    except Exception:
        return ""

@dataclass
class RecorderState:
    recording: bool = False
    frames_q: queue.Queue = queue.Queue()
    stream: sd.InputStream | None = None

class SapiTTSWorker:
    def __init__(self):
        self._q: queue.Queue[str | None] = queue.Queue()
        self.voice_id: str | None = None
        self.output_device_desc: str | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=False)
    def start(self):
        self.thread.start()
        self.ready.wait(timeout=5)
    def speak(self, text: str):
        if not text: return
        print(f"🔊 TTS enqueue ({len(text)} chars)")
        self._q.put(text)
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
                    print("🔈 TTS speaking..."); voice.Speak(item); print("✅ TTS done")
                finally: self._q.task_done()
        except Exception as e: print(f"ℹ️ TTS 스레드 오류: {e}"); self.ready.set()
        finally:
            try:
                if pc is not None: pc.CoUninitialize()
            except Exception: pass

class TypecastTTSWorker:
    def __init__(self):
        self._q: queue.Queue[str | None] = queue.Queue()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=False)
    def start(self):
        self.thread.start(); self.ready.wait(timeout=5)
    def speak(self, text: str):
        if text: print(f"🔊 TTS enqueue ({len(text)} chars)"); self._q.put(text)
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
                    payload = {"voice_id": voice_id, "text": item, "model": model, "language": language, "output": {"volume": 100, "audio_pitch": 0, "audio_tempo": 1.0, "audio_format": audio_format}}
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
                 emotion_queue: Optional[queue.Queue] = None,
                 hotword_queue: Optional[queue.Queue] = None,
                 stop_event: Optional[threading.Event] = None):
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
                "stop=춤을 '멈추라'는 명령/요청/승인. "
                "chat=일반 대화(질문/잡담/설명/감정표현/춤에 대한 견해·가정적 질문 포함). "
                "부정/금지/거절 표현(예:'춤 추지 마','춤은 안돼','그만두지 말고 계속')은 정확히 반영하라. "
                "오직 아래 JSON만 출력:\n"
                '{ "intent": "dance|stop|chat", "normalized_text": "<의미만 보존한 간결한 문장>", '
                '"speakable_reply": "<의도가 chat일 때 1~2문장 공감형 짧은 답변. dance/stop이면 빈 문자열>" }'
            ),
            generation_config={"response_mime_type": "application/json", "temperature": 0.2}
        )
        
        self.start_dance_cb = start_dance_cb
        self.stop_dance_cb  = stop_dance_cb
        self.emotion_queue = emotion_queue
        self.hotword_queue = hotword_queue
        self.stop_event = stop_event or threading.Event()
        
        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 수정된 부분 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        self.last_activity_time = 0
        self.current_listener = None
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        default_engine = "sapi" if IS_WINDOWS else "typecast"
        engine = _get_env("TTS_ENGINE", default_engine).lower()
        if engine == "sapi" and not IS_WINDOWS: engine = "typecast"
        if engine == "typecast": self.tts = TypecastTTSWorker()
        else: self.tts = SapiTTSWorker()
        self.tts.start()

        self.state = RecorderState()
        self._print_intro()
        if ENABLE_GREETING:
            self.tts.speak(GREETING_TEXT)
            if self.emotion_queue: self.emotion_queue.put("NEUTRAL")

    def _print_intro(self):
        print("\n=== Gemini PTT (통합 버전) ===")
        print("▶ '안녕 모티'로 호출(SLEEPY 상태) → 스페이스바로 대화(NEUTRAL 상태) → ESC로 종료")
        print("▶ [User ] 전사 결과 / [Gemini] 모델 답변")
        print("▶ 키워드: '춤' → 댄스 시작 / '그만' → 댄스 정지")
        print(f"▶ MODEL={MODEL_NAME}, SR={SAMPLE_RATE}Hz")
        v_id, out_desc = getattr(self.tts, "voice_id", None), getattr(self.tts, "output_device_desc", None)
        if v_id: print(f"▶ TTS Voice : {v_id}")
        if out_desc: print(f"▶ TTS Output: {out_desc}")
        print("----------------------------------------------------------------\n")

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
            if intent not in ("dance", "stop", "chat"): intent = "chat"
            return {"intent": intent, "normalized_text": str(data.get("normalized_text", text)), "speakable_reply": str(data.get("speakable_reply", "")) if intent == "chat" else ""}
        except Exception as e:
            print(f"(router 폴백) {e}")
            low = text.lower()
            if any(neg in text for neg in ["하지 마", "하지마", "안돼", "안 돼", "그만두지 마", "멈추지 마"]): return {"intent": "chat", "normalized_text": text, "speakable_reply": ""}
            if "그만" in text: return {"intent": "stop", "normalized_text": text, "speakable_reply": ""}
            if "춤" in text: return {"intent": "dance", "normalized_text": text, "speakable_reply": ""}
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
                    try: self.start_dance_cb()
                    except Exception as e: print(f"⚠️ start_dance_cb 실행 오류: {e}")
                
                if self.emotion_queue:
                    chosen_emotion = random.choice(["EXCITED"])
                    self.emotion_queue.put(chosen_emotion)
                    print(f"💃 춤 시작! 표정을 {chosen_emotion}로 변경합니다.")

                model_text = "네! 모티가 춤을 춰볼게요"; speak_text = "네! 모티가 춤을 춰볼게요"

            elif intent == "stop":
                print("💡 의도: DANCE STOP")
                if callable(self.stop_dance_cb):
                    try: self.stop_dance_cb()
                    except Exception as e: print(f"⚠️ stop_dance_cb 실행 오류: {e}")
                
                if self.emotion_queue:
                    self.emotion_queue.put("NEUTRAL")

                model_text = "(춤 정지 명령 처리)"

            print(f"[{ts}] [Gemini] {model_text}\n")
            if speak_text: self.tts.speak(speak_text)
            
        except Exception as e: print(f"❌ 처리 실패: {e}\n")
        finally:
            if self.emotion_queue:
                self.emotion_queue.put("RESET_SLEEPY_TIMER")

    def _on_press(self, key):
        if self.stop_event.is_set(): return False
        try:
            if key == keyboard.Key.space: self._start_recording()
        except Exception as e: print(f"[키 처리 오류 on_press] {e}", file=sys.stderr)

    def _on_release(self, key):
        if self.stop_event.is_set(): return False
        try:
            if key == keyboard.Key.space:
                # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 수정된 부분 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
                self.last_activity_time = time.time()
                # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                self._stop_recording_and_transcribe()
            elif key == keyboard.Key.esc:
                print("ESC 감지 -> 종료 신호 보냄")
                if self.current_listener and self.current_listener.is_alive():
                    self.current_listener.stop()
                self.stop_event.set()
                return False 
        except Exception as e: print(f"[키 처리 오류 on_release] {e}", file=sys.stderr)

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 수정된 부분 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    def run(self):
        while not self.stop_event.is_set():
            print("▶ '안녕 모티' 호출(SLEEPY 상태에서)을 기다립니다... (종료: ESC)")
            try:
                signal = self.hotword_queue.get(timeout=1.0)
                
                if signal == "hotword_detected" and not self.stop_event.is_set():
                    print("💡 핫워드 감지! 대화 세션을 시작합니다.")
                    if self.emotion_queue: self.emotion_queue.put("WAKE")
                    self.tts.speak("네, 말씀하세요.")
                    
                    self.last_activity_time = time.time()
                    self.current_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
                    self.current_listener.start()
                    
                    # 25초(wake 3초 + neutral 20초 + 여유 2초) 동안 대기
                    while time.time() - self.last_activity_time < 25:
                        if self.stop_event.is_set():
                            break
                        time.sleep(0.1)

                    if self.current_listener.is_alive():
                        self.current_listener.stop()
                    
                    if not self.stop_event.is_set():
                         print("▶ 대화 세션 시간 초과. 다시 핫워드 대기 상태로 전환합니다.")

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
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲