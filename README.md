# 🤖 HLRI-IUA MotiRobotics

> **Multi-modal Interaction을 위한 통합 로보틱스 시스템**

본 프로젝트는 실시간 비전 프로세싱과 **ART(Adaptive Resonance Theory)** 알고리즘을 융합하여 단 한 번의 학습만으로 사용자를 정밀하게 식별 및 기억(**One-Shot Learning**)하는 지능형 로봇 시스템입니다.

특히, **비동기 멀티스레드 아키텍처(Asynchronous Multi-threaded Architecture)**를 도입하여 시스템의 동시성(Concurrency)과 반응 속도를 극대화했습니다. Vision, Voice, Control 등 각 모듈을 독립적인 스레드로 병렬 실행하고, 데이터 흐름을 **메시지 큐(Message Queue)**로 관리하여 프로세스 간 병목 현상을 제거했습니다.

또한, 대화 데이터를 실시간으로 디스크에 쓰지 않고 **인메모리 버퍼링(In-memory Buffering)** 후 세션 종료 시 일괄 저장(**Batch Processing**)하는 최적화 기법을 적용하여, I/O로 인한 지연(Blocking) 없이 **인지(Perception)-판단(Cognition)-행동(Action)**이 실시간으로 연결되는 고성능 상호작용 루프를 완성했습니다.

---

## ✨ 주요 기능

### 🤝 자연스러운 상호작용 (Natural Interaction)
사용자의 손짓(Gesture)이나 움직임을 실시간으로 인식하여 반응합니다. 로봇의 시선이 사용자의 얼굴을 따라가는 **Face Tracking** 기술과 동작 인식을 통해 역동적인 상호작용을 제공합니다.

### 💬 LLM 기반 대화 시스템 (LLM-based Conversation)
**Google Gemini API**를 활용하여 정해진 답변이 아닌, 사용자의 맥락과 상황을 이해하는 자유로운 대화가 가능합니다. **하이브리드 라우팅(Hybrid Routing)**을 통해 단순 명령은 즉각 처리하고, 복잡한 대화는 LLM이 처리하여 효율성을 높였습니다.

### 🧠 사용자 식별 및 기억 (User Recognition & Memory)
MediaPipe와 **ART(Adaptive Resonance Theory)** 알고리즘을 결합하여, **단 1장의 사진만으로도 사용자를 즉시 등록하고 재인식**합니다. 대화 내용을 요약하여 JSON으로 관리함으로써, 이전 대화 맥락을 기억하고 연속적인 대화를 이어갑니다.

### 👀 실시간 반응성 (Real-time Response)
**비동기(Asynchronous)** 아키텍처를 적용하여 대화 처리 중에도 끊김 없는 아이컨택(Eye-contact)과 **60FPS**의 부드러운 표정 변화를 유지합니다.

---

## 📂 디렉토리 구조

프로젝트는 기능별로 명확하게 분리된 모듈 구조를 가집니다.

```text
MOTIROBOTICS/
├── display/                    # 🖥️ 디스플레이 및 청각 모듈
│   ├── emotions/               # 🎨 감정별 표정 렌더링 (happy.py, sad.py 등)
│   ├── fonts/                  # 🔤 UI 폰트 리소스
│   ├── hotword_model/          # 🎤 호출어 감지 모델 데이터
│   ├── hotword.py              # Wake-word("안녕 모티") 감지 로직
│   └── subtitle.py             # 화면 자막 및 사용자 UI 출력
├── function/                   # ⚙️ 로봇 핵심 제어 및 스킬 구현
│   ├── config.py               # 로봇 설정값 (포트, 상수 등)
│   ├── dxl_io.py               # Dynamixel 모터 제어 (PID 및 관절 제어)
│   ├── vision_brain.py         # MediaPipe 기반 비전/얼굴 인식 로직
│   ├── gesture_recognizer.task # 제스처 인식 AI 모델 파일
│   ├── wheel.py                # 모바일 베이스 구동 제어
│   ├── profile_manager.py      # 사용자 기억/프로필 관리
│   ├── ox_game.py              # OX 퀴즈 게임 로직
│   ├── rock_paper.py           # 가위바위보 게임 로직
│   └── dance.py                # 댄스 모션 스크립트
├── logs/                       # 📝 시스템 실행 로그 저장소
├── models/                     # 🧠 ART 알고리즘 학습 모델 저장
├── art_brain_manage.py         # 🤖 로봇 상태 관리자 (State Machine)
├── debug_motor_positions.py    # 🔧 모터 위치 디버깅 및 캘리브레이션
├── gemini_api.py               # 💬 LLM (Gemini) 통신 및 대화 엔진
├── launcher.py                 # 🚀 전체 시스템 실행 파일 (System Supervisor)
├── user_profiles.json          # 💾 사용자 장기 기억 데이터베이스
├── requirements.txt            # 📦 프로젝트 의존성 라이브러리 목록
└── .env                        # 🔑 API Key 및 환경 변수 설정
```

## 🛠 설치 및 실행 방법

본 가이드는 **Windows 10/11** 및 **Python 3.11** 환경을 기준으로 작성되었습니다.

### 1. 사전 준비
* **OS:** Windows 10 또는 11
* **Python:** Python 3.11 설치 필수
* **Hardware:** 웹캠(Webcam), 마이크, Dynamixel 모터(X-series) 및 U2D2 통신 장비
* **API Key:** Google Gemini API 키 (필수), Typecast API Key (선택)

### 2. 프로젝트 클론 및 라이브러리 설치
PowerShell을 열고 아래 명령어를 순서대로 입력하세요.

**Step 1: 저장소 클론**
```powershell
git clone [https://github.com/HandongSF/hlri-iua-motirobotics.git](https://github.com/HandongSF/hlri-iua-motirobotics.git)
cd hlri-iua-motirobotics
```
**Step 2: Python 버전 확인**
```powershell
py -3.11 --version
```
**Step 3: pip 업그레이드 (필수)**
```powershell
py -3.11 -m pip install -U pip wheel
```

**Step 4: 의존성 패키지 설치**
별도의 가상환경 없이 사용자 경로에 설치합니다.
```powershell
py -3.11 -m pip install --user -r requirements.txt
```
> *참고: 설치 후 실행이 안 될 경우, pip가 안내하는 경로를 시스템 PATH에 추가해야 할 수 있습니다.*

### 3. 환경 설정 (.env.local)
프로젝트 루트에 `.env.local` 파일을 생성하고 아래 내용을 작성합니다.

```ini
# [필수] API 및 하드웨어 설정
GOOGLE_API_KEY="your_gemini_api_key"
DXL_PORT="COM3"           # 장치 관리자에서 포트 확인 (예: COM3, COM4)

# [설정] Windows TTS (SAPI 사용 시 무료/기본)
TTS_ENGINE=sapi

# [선택] Typecast API (고품질 음성 사용 시)
TYPECAST_API_KEY="your_typecast_key"
```
## 📎 모델 및 데이터 준비

프로젝트 실행을 위해 필수 AI 모델 파일을 올바른 경로에 위치시켜야 합니다.

### 🔹 1. Vision AI 모델 (ART 핵심 - InsightFace)
빠른 얼굴 인식을 위해 NVIDIA GPU 사용을 권장합니다.
```powershell
pip install onnxruntime-gpu
```
### 🔹 2. Interaction AI 모델 (MediaPipe)
제스처 인식 및 인터랙션을 위한 경량화 모델입니다. `function/` 디렉토리에 위치시킵니다.
* `gesture_recognizer.task`: 손의 움직임(가위, 바위, 보, 손흔들기) 인식.
* `face_landmarker.task`: (선택) 얼굴 랜드마크 및 표정 디테일 추적.

### 🔹 3. Hotword 모델 (Porcupine)
"안녕 모티" 호출어를 감지하기 위한 모델입니다. `display/hotword_model/` 디렉토리에 위치시킵니다.

> **⚠️ 주의:** `.ppn` 모델 파일은 PC마다 고유하거나 계정에 종속될 수 있어, 단순 복사 시 작동하지 않을 수 있습니다. 각 개발자가 직접 발급받아야 합니다.

1.  [Picovoice Console](https://console.picovoice.ai/)에 가입 및 로그인합니다.
2.  **Porcupine** 메뉴에서 "안녕 모티" 키워드를 생성하고 **Windows (ko)** 플랫폼으로 다운로드합니다.
3.  다운로드한 파일을 아래 이름으로 변경하여 해당 경로에 저장합니다.
    * `porcupine_params_ko.pv`: (라이브러리에 포함되거나 콘솔에서 함께 제공)
    * `안녕-모티_ko_windows_v3_0_0.ppn`: 다운로드 받은 모델 파일

---

## 🚀 로봇 실행

모든 설정이 완료되었다면, 시스템 슈퍼바이저인 `launcher.py`를 실행하여 인지, 판단, 제어 모듈을 모두 가동합니다.

```powershell
py -3.11 launcher.py
```

*(※ 소스 코드 위치에 따라 `src/launcher.py` 대신 루트의 `launcher.py`를 실행합니다)*

터미널에 **"System Ready"** 로그가 출력되면, **"안녕 모티"** 라고 불러 상호작용을 시작하세요.

---

## ⚙️ 작동 원리 (아키텍처)

이 로봇은 **인지(Perception) - 판단(Cognition) - 표현(Action)** 의 3계층 구조로 작동합니다.

1.  **Perception (인지)**
    * 마이크와 카메라를 통해 사용자의 음성, 얼굴 좌표, 제스처 데이터를 수집합니다.
    * **Multi-modal Trigger** 방식을 사용하여 음성과 시각 정보를 융합해 인식률을 높입니다.

2.  **Cognition (판단)**
    * **LLM Engine:** 입력된 텍스트와 상황을 분석하여 자연스러운 대화와 행동을 생성합니다.
    * **State Manager:** 로봇의 현재 상태(Idle, Active, Sleep 등)를 관리하고 적절한 작업을 스케줄링합니다.

3.  **Action (표현)**
    * **Visual:** 현재 감정 상태에 맞는 표정을 디스플레이에 실시간으로 렌더링합니다.
    * **Physical:** PID 제어를 통해 얼굴을 추적(Tracking)하거나, 제스처 및 게임 동작을 물리적으로 수행합니다.
