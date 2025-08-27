# gemini_v2.py
# Windows + Python 3.11.6
# 스페이스바 누르는 동안 녹음 → 떼면 전사 → Gemini 답변 생성 → 선택된 TTS로 읽기
# (NEW) 키워드 콜백: "춤" → start_dance_cb(), "그만" → stop_dance_cb()
# (NEW) TTS 규칙: '춤'이면 고정 멘트만 말하기, '그만'이면 말하지 않기

import os
import io
import sys
import base64
import queue
import threading
import wave
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

# --- .env.local 로드 ---
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
import requests  # <-- Typecast REST

# ---- Windows SAPI COM (직접) ----
import pythoncom
import win32com.client

# ---------------------- 설정 ----------------------
def _get_env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip()

def _find_input_device_by_name(name_substr: str) -> int | None:
    """입력 장치 이름 '부분일치'로 인덱스 찾기 (대소문자 무시)"""
    if not name_substr:
        return None
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
    "다음 오디오를 한국어로 정확히 전사해줘. "
    "사람의 목소리를 제외한 다른 소음은 무시해줘. "
    "잡음처럼 느껴지는 것들은 무시해줘."
    "문장부호와 띄어쓰기를 자연스럽게 해줘."
)

SYSTEM_INSTRUCTION = _get_env(
    "SYSTEM_INSTRUCTION",
    "너는 사용자의 감정을 분석하고 공감해주는 친절한 감정 서비스 로봇이야. 너의 이름은 모티. 사용자 발화에 1~2문장으로 명확하게 답해. "
    "사실이 불확실하면 추측하지 말고 추가 정보를 요청해."
)

# --- TTS 옵션 (SAPI용) ---
TTS_RATE = int(_get_env("TTS_RATE", "0"))          # SAPI: -10..10
TTS_VOLUME = int(_get_env("TTS_VOLUME", "100"))    # SAPI: 0..100
TTS_FORCE_VOICE_ID = _get_env("TTS_FORCE_VOICE_ID", "")
TTS_OUTPUT_DEVICE = _get_env("TTS_OUTPUT_DEVICE", "")  # 출력 장치 이름(일부 포함 매칭)
# --------------------------------------------------


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
    """
    Windows SAPI를 전용 스레드에서 직접 사용.
    - 음성/출력 장치 선택 지원
    - 큐의 모든 텍스트를 읽고 종료
    """
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
        if not text:
            return
        print(f"🔊 TTS enqueue ({len(text)} chars)")
        self._q.put(text)

    def close_and_join(self, drain: bool = True, timeout: float = 15.0):
        try:
            if drain:
                print("⏳ TTS 대기: 큐 비우는 중...")
                self._q.join()
            self._q.put(None)
            self.thread.join(timeout=timeout)
        except Exception:
            pass

    def _run(self):
        try:
            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")  # SAPI.SpVoice
            # --- Voice 선택 ---
            voices = voice.GetVoices()
            chosen_voice_id = None

            if TTS_FORCE_VOICE_ID:
                for i in range(voices.Count):
                    v = voices.Item(i)
                    if v.Id == TTS_FORCE_VOICE_ID:
                        chosen_voice_id = v.Id
                        break
                if not chosen_voice_id:
                    print(f"ℹ️ TTS_FORCE_VOICE_ID를 찾지 못했습니다: {TTS_FORCE_VOICE_ID}")

            if not chosen_voice_id:
                # ko/korean/한국어 포함 우선
                for i in range(voices.Count):
                    v = voices.Item(i)
                    blob = f"{v.Id} {v.GetDescription()}".lower()
                    if any(t in blob for t in ["ko", "korean", "한국어"]):
                        chosen_voice_id = v.Id
                        break
                if not chosen_voice_id and voices.Count > 0:
                    chosen_voice_id = voices.Item(0).Id

            if chosen_voice_id:
                # Set by token
                for i in range(voices.Count):
                    v = voices.Item(i)
                    if v.Id == chosen_voice_id:
                        voice.Voice = v
                        self.voice_id = v.Id
                        break

            # --- 출력 장치 선택 ---
            outs = voice.GetAudioOutputs()
            chosen_out_desc = None
            if TTS_OUTPUT_DEVICE:
                key = TTS_OUTPUT_DEVICE.lower()
                for i in range(outs.Count):
                    o = outs.Item(i)
                    desc = o.GetDescription()
                    if key in desc.lower():
                        voice.AudioOutput = o
                        chosen_out_desc = desc
                        break
                if not chosen_out_desc:
                    print(f"ℹ️ 지정한 출력 장치를 찾지 못했습니다: {TTS_OUTPUT_DEVICE}")

            if not chosen_out_desc and outs.Count > 0:
                try:
                    desc = outs.Item(0).GetDescription()
                except Exception:
                    desc = "System Default"
                chosen_out_desc = desc

            self.output_device_desc = chosen_out_desc

            # --- 속도/볼륨 설정 ---
            try:
                voice.Rate = max(-10, min(10, TTS_RATE))
            except Exception:
                pass
            try:
                voice.Volume = max(0, min(100, TTS_VOLUME))
            except Exception:
                pass

            # 참고 정보 출력
            print("🎧 사용 가능한 음성 목록 (SAPI):")
            for i in range(voices.Count):
                v = voices.Item(i)
                print(f"  - [{i}] id='{v.Id}', desc='{v.GetDescription()}'")
            print("🔉 사용 가능한 출력 장치 (SAPI):")
            for i in range(outs.Count):
                o = outs.Item(i)
                print(f"  - [{i}] '{o.GetDescription()}'")
            print(f"▶ 선택된 음성 id='{self.voice_id}'")
            print(f"▶ 선택된 출력='{self.output_device_desc}'")

            self.ready.set()

            # 초기 테스트 한 줄
            voice.Speak("안녕하세요. T T S가 준비되었습니다.")

            # 큐 루프
            while True:
                item = self._q.get()
                if item is None:
                    self._q.task_done()
                    break
                try:
                    print("🔈 TTS speaking...")
                    # 동기 재생
                    voice.Speak(item)
                    print("✅ TTS done")
                finally:
                    self._q.task_done()

        except Exception as e:
            print(f"ℹ️ TTS 스레드 오류: {e}")
            self.ready.set()
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


# ======================= Typecast 전용 워커 =======================
class TypecastTTSWorker:
    """
    Typecast REST API로 합성 → WAV를 메모리에서 재생.
    필요 env:
      TYPECAST_API_KEY, TYPECAST_VOICE_ID (필수)
      TYPECAST_MODEL=ssfm-v21 (기본)
      TYPECAST_LANGUAGE=kor   (기본)
      TYPECAST_AUDIO_FORMAT=wav (기본)
      TYPECAST_EMOTION / TYPECAST_EMOTION_INTENSITY (선택)
      TYPECAST_SEED (선택)
    """
    def __init__(self):
        self._q: queue.Queue[str | None] = queue.Queue()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=False)

    def start(self):
        self.thread.start()
        self.ready.wait(timeout=5)

    def speak(self, text: str):
        if text:
            print(f"🔊 TTS enqueue ({len(text)} chars)")
            self._q.put(text)

    def close_and_join(self, drain: bool = True, timeout: float = 30.0):
        try:
            if drain:
                self._q.join()
            self._q.put(None)
            self.thread.join(timeout=timeout)
        except Exception:
            pass

    def _run(self):
        try:
            api_key = _get_env("TYPECAST_API_KEY")
            voice_id = _get_env("TYPECAST_VOICE_ID")
            if not api_key or not voice_id:
                print("❗ TYPECAST_API_KEY 또는 TYPECAST_VOICE_ID가 비어있습니다.")
                self.ready.set()
                return

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
                if item is None:
                    self._q.task_done(); break
                try:
                    payload = {
                        "voice_id": voice_id,
                        "text": item,
                        "model": model,
                        "language": language,
                        "output": {
                            "volume": 100,
                            "audio_pitch": 0,
                            "audio_tempo": 1.0,
                            "audio_format": audio_format
                        }
                    }
                    if emotion:
                        payload["prompt"] = {
                            "emotion_preset": emotion,
                            "emotion_intensity": intensity
                        }
                    if seed is not None:
                        payload["seed"] = seed

                    r = requests.post(url, headers=headers, json=payload, timeout=60)
                    if r.status_code == 200:
                        data = r.content  # audio/wav bytes
                        with io.BytesIO(data) as buf:
                            with wave.open(buf, "rb") as wf:
                                sr = wf.getframerate()
                                sampwidth = wf.getsampwidth()
                                frames = wf.readframes(wf.getnframes())
                        # 16-bit PCM 가정
                        if sampwidth == 2:
                            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                        else:
                            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                        sd.play(audio, sr); sd.wait()
                        print("✅ TTS done")
                    else:
                        print(f"❌ Typecast 오류 {r.status_code}: {r.text[:200]}")
                finally:
                    self._q.task_done()
        except Exception as e:
            print(f"ℹ️ Typecast TTS 스레드 오류: {e}")
            self.ready.set()


class PressToTalk:
    def __init__(self,
                 start_dance_cb: Optional[Callable[[], None]] = None,
                 stop_dance_cb: Optional[Callable[[], None]] = None):
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key or not api_key.strip():
            print("❗ GOOGLE_API_KEY가 없습니다.")
            print("   - .env.local 예: GOOGLE_API_KEY=AIzxxxxxxxxx")
            print("   - 또는 PowerShell: $env:GOOGLE_API_KEY=\"<키>\" 후 실행")
            sys.exit(1)

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(MODEL_NAME)
        self.chat = genai.GenerativeModel(
            MODEL_NAME,
            system_instruction=SYSTEM_INSTRUCTION
        ).start_chat(history=[])

        # --- 키워드 콜백 저장 ---
        self.start_dance_cb = start_dance_cb
        self.stop_dance_cb  = stop_dance_cb

        # --- TTS 엔진 선택 ---
        engine = _get_env("TTS_ENGINE", "sapi").lower()
        if engine == "typecast":
            self.tts = TypecastTTSWorker()
        else:
            self.tts = SapiTTSWorker()
        self.tts.start()

        self.state = RecorderState()
        self.listener = None
        self._print_intro()

    def _print_intro(self):
        print("\n=== Gemini Press-to-Transcribe + Chat + TTS (Windows, Python 3.11) ===")
        print("▶ 스페이스바 누르는 동안 녹음 → 떼면 전사 + 답변 생성 + 음성 재생")
        print("▶ [User ] 전사 결과 / [Gemini] 모델 답변")
        print("▶ ESC 로 종료 (답변 읽기 완료 후 종료)")
        print("▶ 키워드: '춤' → 5번 모터 댄스 시작 / '그만' → 댄스 정지·원위치")
        print(f"▶ MODEL={MODEL_NAME}, SR={SAMPLE_RATE}Hz, CH={CHANNELS}, DTYPE={DTYPE}")
        v_id = getattr(self.tts, "voice_id", None)
        out_desc = getattr(self.tts, "output_device_desc", None)
        if v_id:
            print(f"▶ TTS Voice : {v_id}")
        if out_desc:
            print(f"▶ TTS Output: {out_desc}")
        print("----------------------------------------------------------------\n")

    # ====== 오디오 캡처 ======
    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[오디오 경고] {status}", file=sys.stderr)
        self.state.frames_q.put(indata.copy())

    def _start_recording(self):
        if self.state.recording:
            return
        while not self.state.frames_q.empty():
            try:
                self.state.frames_q.get_nowait()
            except queue.Empty:
                break

        # ----- 입력 장치 선택: 인덱스 → 이름 → 기본 -----
        device_idx = None
        env_dev = os.environ.get("INPUT_DEVICE_INDEX")
        if env_dev and env_dev.strip():
            try:
                device_idx = int(env_dev.strip())
            except Exception:
                device_idx = None

        if device_idx is None:
            env_name = os.environ.get("INPUT_DEVICE_NAME", "")
            if env_name:
                device_idx = _find_input_device_by_name(env_name)

        # (선택) 어떤 장치가 선택됐는지 로그
        try:
            if device_idx is not None:
                dinfo = sd.query_devices(device_idx, 'input')
                print(f"🎚️  선택한 입력 장치: [{device_idx}] {dinfo['name']} | default_sr={dinfo.get('default_samplerate')}")
            else:
                default_in = sd.default.device[0]
                dinfo = sd.query_devices(default_in, 'input')
                print(f"🎚️  시스템 기본 입력 사용: [{default_in}] {dinfo['name']} | default_sr={dinfo.get('default_samplerate')}")
        except Exception:
            pass

        self.state.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._audio_callback,
            blocksize=0,
            device=device_idx
        )
        self.state.stream.start()
        self.state.recording = True
        print("🎙️  녹음 시작 (스페이스바 유지 중)...")

    def _stop_recording_and_transcribe(self):
        if not self.state.recording:
            return
        print("⏹️  녹음 종료, 전사 중...")
        self.state.recording = False

        try:
            if self.state.stream:
                self.state.stream.stop()
                self.state.stream.close()
        finally:
            self.state.stream = None

        chunks = []
        while not self.state.frames_q.empty():
            chunks.append(self.state.frames_q.get())

        if not chunks:
            print("(녹음 데이터가 없습니다. 다시 시도해 주세요.)\n")
            return

        audio_np = np.concatenate(chunks, axis=0)
        wav_bytes = self._to_wav_bytes(audio_np, SAMPLE_RATE, CHANNELS, DTYPE)

        threading.Thread(
            target=self._transcribe_then_chat, args=(wav_bytes,), daemon=True
        ).start()

    @staticmethod
    def _to_wav_bytes(audio_np: np.ndarray, samplerate: int, channels: int, dtype: str) -> bytes:
        with io.BytesIO() as buf:
            with wave.open(buf, 'wb') as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(np.dtype(dtype).itemsize)
                wf.setframerate(samplerate)
                wf.writeframes(audio_np.tobytes())
            return buf.getvalue()

    # ----------- 사용자 키워드 처리(=TTS 정책 포함) -----------
    def _handle_user_keywords(self, text: str) -> str | None:
        """
        반환값:
          - 'dance' : 춤 시작(고정 멘트 TTS)
          - 'stop'  : 그만(아무 말도 안함)
          - None    : 키워드 없음
        우선순위: '그만' > '춤'
        """
        if not text:
            return None
        if "그만" in text:
            print("💡 키워드 감지: '그만' → DANCE STOP 요청")
            if callable(self.stop_dance_cb):
                try: self.stop_dance_cb()
                except Exception as e: print(f"⚠️ stop_dance_cb 실행 오류: {e}")
            return "stop"
        if "춤" in text:
            print("💡 키워드 감지: '춤' → DANCE START 요청")
            if callable(self.start_dance_cb):
                try: self.start_dance_cb()
                except Exception as e: print(f"⚠️ start_dance_cb 실행 오류: {e}")
            return "dance"
        return None

    def _transcribe_then_chat(self, wav_bytes: bytes):
        """오디오 → 전사 → 모델 답변 생성 → (규칙에 따라) TTS 재생"""
        try:
            b64 = base64.b64encode(wav_bytes).decode("ascii")
            parts = [
                {"text": PROMPT_TEXT},
                {"inline_data": {"mime_type": "audio/wav", "data": b64}},
            ]
            resp = self.model.generate_content(parts)
            user_text = _extract_text(resp)
            if not user_text:
                print("📝 전사 결과가 비어 있습니다.\n")
                return

            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] [User ] {user_text}")

            # 사용자 발화에서 키워드 처리 (TTS 정책 포함)
            action = self._handle_user_keywords(user_text)

            # 모델 응답은 항상 생성(로그/콘솔용)하되,
            # TTS는 action 규칙에 따라 선택/대체/무음 처리
            reply = self.chat.send_message(user_text)
            model_text = _extract_text(reply) or ""
            print(f"[{ts}] [Gemini] {model_text}\n")

            # ====== TTS 규칙 ======
            if action == "dance":
                # 생성 응답 대신 고정 멘트만 말하기
                self.tts.speak("네! 모티가 춤을 춰볼게요")
            elif action == "stop":
                # 아무 말도 하지 않음
                pass
            else:
                # 평소처럼 모델 응답 말하기
                if model_text:
                    self.tts.speak(model_text)

        except Exception as e:
            print(f"❌ 처리 실패: {e}\n")

    # ----------------- 키보드 핸들러 -----------------
    def _on_press(self, key):
        try:
            if key == keyboard.Key.space:
                self._start_recording()
        except Exception as e:
            print(f"[키 처리 오류 on_press] {e}", file=sys.stderr)

    def _on_release(self, key):
        try:
            if key == keyboard.Key.space:
                self._stop_recording_and_transcribe()
            elif key == keyboard.Key.esc:
                print("종료합니다. 👋  (답변 읽기 완료까지 잠시만요)")
                self.tts.close_and_join(drain=True)
                return False
        except Exception as e:
            print(f"[키 처리 오류 on_release] {e}", file=sys.stderr)

    def run(self):
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as self.listener:
            self.listener.join()


if __name__ == "__main__":
    try:
        default_in = sd.default.device[0]
        sr = sd.query_devices(default_in, 'input')['default_samplerate']
        if abs(sr - SAMPLE_RATE) > 1:
            print(f"ℹ️ 참고: 기본 입력 장치 표준 샘플레이트={sr:.0f}Hz, 스크립트={SAMPLE_RATE}Hz")
    except Exception:
        pass

    app = PressToTalk()
    app.run()
