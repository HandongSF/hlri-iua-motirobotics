# function/vision_brain.py

import os
import pickle
import numpy as np
from collections import deque, Counter
from insightface.app import FaceAnalysis

# ==========================================
# ⚙️ 설정
# ==========================================
RHO = 0.80          # 경계심 (0~1). 높을수록 엄격하게 구분
ALPHA = 1e-5        # 선택 파라미터
BETA = 0.1          # 학습률
BUFFER_SIZE = 5     # 인식 안정화 버퍼 크기
VOTE_THRESHOLD = 3  # 투표 임계값
DB_FILE = "art_brain.pkl"

class FuzzyART:
    def __init__(self, rho=RHO, alpha=ALPHA, beta=BETA):
        self.rho = rho
        self.alpha = alpha
        self.beta = beta
        self.W = []     # 기억된 패턴들 (가중치)
        self.labels = [] # 각 패턴의 이름
        self.num_categories = 0

    def _complement_coding(self, x):
        x = np.clip(x, -1, 1)
        x_norm = (x + 1) / 2
        return np.concatenate((x_norm, 1 - x_norm))

    def predict(self, x):
        if self.num_categories == 0:
            return "Unknown", -1

        I = self._complement_coding(x)
        scores = []
        for w in self.W:
            intersection = np.minimum(I, w)
            score = np.sum(intersection) / (self.alpha + np.sum(w))
            scores.append(score)

        sorted_indices = np.argsort(scores)[::-1]
        norm_I = np.sum(I)

        for j in sorted_indices:
            w = self.W[j]
            intersection = np.minimum(I, w)
            if (np.sum(intersection) / norm_I) >= self.rho:
                return self.labels[j], j
        
        return "Unknown", -1

    def learn(self, x, label):
        I = self._complement_coding(x)
        pred_label, idx = self.predict(x)

        # 기존 기억 강화 (Update)
        if idx != -1 and pred_label == label:
            self.W[idx] = self.beta * np.minimum(I, self.W[idx]) + (1 - self.beta) * self.W[idx]
            return f"Updated memory for {label}"

        # 새 기억 생성 (Create)
        self.W.append(I)
        self.labels.append(label)
        self.num_categories += 1
        return f"Created new memory for {label}"

class RobotBrain:
    def __init__(self, db_path=None, similarity_threshold=None):
        # (호환성을 위해 인자 추가, 사용은 안 함)
        print("⏳ Vision Brain(InsightFace + FuzzyART) 초기화 중...")
        
        # ▼▼▼ [수정 1] 필요한 모듈만 지정하여 로드 (속도 향상) ▼▼▼
        # allowed_modules=['detection', 'recognition'] 만 사용
        self.app = FaceAnalysis(
            name='buffalo_l', 
            allowed_modules=['detection', 'recognition'], 
            providers=['CUDAExecutionProvider', 'CPUExecutionProvider']
        )
        self.app.prepare(ctx_id=0, det_size=(640, 640))
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        # Fuzzy ART (뇌)
        self.art = FuzzyART()
        self.load_brain()
        
        self.buffer = deque(maxlen=BUFFER_SIZE)
        print(f"✅ Vision Brain 준비 완료. (기억된 얼굴 수: {self.art.num_categories})")

    def load_brain(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'rb') as f:
                    data = pickle.load(f)
                    self.art.W = data['W']
                    self.art.labels = data['labels']
                    self.art.num_categories = len(data['labels'])
            except Exception as e:
                print(f"⚠️ 브레인 로드 실패: {e}")

    def save_brain(self):
        try:
            with open(DB_FILE, 'wb') as f:
                pickle.dump({'W': self.art.W, 'labels': self.art.labels}, f)
            print("💾 얼굴 기억(Brain) 저장 완료.")
        except Exception as e:
            print(f"❌ 얼굴 기억 저장 실패: {e}")

    def recognize_face(self, frame):
        """
        프레임을 받아 얼굴 벡터를 추출하고, FuzzyART로 누구인지 식별합니다.
        Return: (embedding, predicted_name)
        """
        # [참고] CPU 모드일 때 여기서 시간이 가장 많이 소요됩니다.
        # face.py에서 1초에 한 번만 호출하도록 제한했으므로 이제 괜찮을 것입니다.
        faces = self.app.get(frame)
        
        if len(faces) == 0:
            self.buffer.append(None)
            return None, None

        # 화면 중앙에 가장 가까운 얼굴 선택
        h, w, _ = frame.shape
        cx = w // 2
        target = min(faces, key=lambda f: abs((f.bbox[0]+f.bbox[2])/2 - cx))
        embedding = target.embedding

        # 뇌에 물어보기
        raw_name, _ = self.art.predict(embedding)
        self.buffer.append(raw_name)

        # 투표 (안정화)
        valid = [n for n in self.buffer if n is not None]
        if valid:
            common, cnt = Counter(valid).most_common(1)[0]
            if cnt >= VOTE_THRESHOLD:
                return embedding, common
        
        return embedding, "Thinking..."

    def register_face(self, embedding, name):
        """외부에서 이름이 확인되면 기억에 등록"""
        if embedding is None: return "No face"
        msg = self.art.learn(embedding, name)
        self.save_brain() # 즉시 저장
        return msg