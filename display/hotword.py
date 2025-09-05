import os
import threading
import pyaudio
import pvporcupine
import struct
from dotenv import load_dotenv

class HotwordDetector(threading.Thread):
    def __init__(self, hotword_queue):
        super().__init__(daemon=True)
        self.hotword_queue = hotword_queue
        self.listen_event = threading.Event()
        self.should_run = True
        self.pa = pyaudio.PyAudio()
        self.audio_stream = None
        self.is_listening = False
        
        load_dotenv(dotenv_path='./.env.local')

        access_key = os.getenv("PICOVOICE_ACCESS_KEY")

        # [수정 1] hotword.py 파일의 현재 위치를 기준으로 절대 경로 생성
        # 이렇게 하면 launcher.py를 어디서 실행하든 경로가 깨지지 않습니다.
        try:
            # 현재 이 스크립트 파일이 있는 디렉토리의 절대 경로를 찾습니다.
            script_dir = os.path.dirname(os.path.abspath(__file__))
            
            # .env.local에서 파일 이름만 읽어옵니다.
            hotword_filename = os.getenv("HOTWORD_FILENAME")
            model_filename = os.getenv("MODEL_FILENAME")

            if not all([hotword_filename, model_filename]):
                 raise ValueError(".env.local에 HOTWORD_FILENAME 또는 MODEL_FILENAME이 없습니다.")

            # 절대 경로를 조합합니다.
            hotword_path = os.path.join(script_dir, 'hotword_model', hotword_filename)
            model_path = os.path.join(script_dir, 'hotword_model', model_filename)

        except Exception as e:
            print(f"오류: 모델 파일 경로를 설정하는 중 문제가 발생했습니다 - {e}")
            self.should_run = False
            return

        if not all([access_key, hotword_path, model_path]):
            print("오류: .env.local 필수 변수가 누락되었거나 파일 경로가 잘못되었습니다.")
            self.should_run = False
            return
        
        # [수정 2] 파일이 실제로 존재하는지 확인하는 코드 추가
        if not os.path.exists(hotword_path):
            print(f"오류: 핫워드 파일을 찾을 수 없습니다: {hotword_path}")
            self.should_run = False
            return
        if not os.path.exists(model_path):
            print(f"오류: 모델 파일을 찾을 수 없습니다: {model_path}")
            self.should_run = False
            return

        self.device_index = None
        device_name_to_find = os.getenv("INPUT_DEVICE_NAME")
        if device_name_to_find:
            print(f"지정된 마이크 검색 중: '{device_name_to_find}'...")
            for i in range(self.pa.get_device_count()):
                device_info = self.pa.get_device_info_by_index(i)
                if device_info.get('maxInputChannels') > 0:
                    if device_name_to_find.lower() in device_info.get('name').lower():
                        self.device_index = i
                        print(f"🎚️  마이크를 찾았습니다: [{i}] {device_info.get('name')}")
                        break
            if self.device_index is None:
                print(f"⚠️  '{device_name_to_find}' 마이크를 찾을 수 없습니다. 시스템 기본 마이크를 사용합니다.")
        else:
            print("🎚️  시스템 기본 마이크를 사용합니다.")

        try:
            self.porcupine = pvporcupine.create(access_key=access_key, keyword_paths=[hotword_path], model_path=model_path)
        except pvporcupine.PorcupineError as e:
            print(f"Porcupine 초기화 오류: {e}")
            self.should_run = False

    # run, _start_listening, _stop_listening, start_detection, stop_detection, stop 메소드는
    # 기존과 동일하므로 수정할 필요 없습니다.
    # ... (이하 모든 코드는 기존과 동일)
    def run(self):
        if not self.should_run: return
        print("Hotword detector thread is ready.")
        while self.should_run:
            self.listen_event.wait()
            if not self.should_run: break
            self._start_listening()
            while self.listen_event.is_set() and self.should_run:
                try:
                    pcm = self.audio_stream.read(self.porcupine.frame_length, exception_on_overflow=False)
                    pcm = struct.unpack_from("h" * self.porcupine.frame_length, pcm)
                    if self.porcupine.process(pcm) >= 0:
                        print("핫워드 감지됨! '안녕 모티'")
                        self.hotword_queue.put("hotword_detected")
                        self.listen_event.clear()
                except (IOError, struct.error):
                    pass
                except Exception as e:
                    print(f"오디오 처리 중 오류: {e}")
                    self.listen_event.clear()
            self._stop_listening()
        if self.porcupine: self.porcupine.delete()
        self.pa.terminate()
        print("Hotword detector thread stopped.")

    def _start_listening(self):
        if not self.is_listening and self.should_run:
            try:
                self.audio_stream = self.pa.open(rate=self.porcupine.sample_rate, channels=1, format=pyaudio.paInt16, input=True, frames_per_buffer=self.porcupine.frame_length, input_device_index=self.device_index)
                self.is_listening = True
                print("오디오 스트림 시작. 핫워드 감지 중.")
            except Exception as e:
                print(f"오디오 스트림 열기 실패: {e}")

    def _stop_listening(self):
        if self.is_listening and self.audio_stream:
            self.is_listening = False
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            self.audio_stream = None
            print("오디오 스트림 중지. 핫워드 감지 대기 중.")

    def start_detection(self):
        self.listen_event.set()

    def stop_detection(self):
        self.listen_event.clear()

    def stop(self):
        self.should_run = False
        self.listen_event.set()
        self.join()