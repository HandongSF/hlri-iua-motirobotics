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
import pickle
import os
import numpy as np # 가중치 벡터가 numpy 배열일 수 있으므로 import 유지

DB_FILE = "art_brain.pkl"

def delete_face_data(name_to_delete: str):
    """
    art_brain.pkl 파일에서 특정 이름과 관련된 얼굴 데이터(가중치 및 레이블)를 삭제합니다.
    """
    if not os.path.exists(DB_FILE):
        print(f"❌ {DB_FILE} 파일이 존재하지 않습니다. 삭제할 데이터가 없습니다.")
        return

    try:
        # 1. 파일 로드
        with open(DB_FILE, 'rb') as f:
            data = pickle.load(f)
            W = data.get('W', [])
            labels = data.get('labels', [])
            
        initial_count = len(labels)
        
        # 2. 삭제할 이름의 인덱스 찾기
        indices_to_delete = [i for i, label in enumerate(labels) if label == name_to_delete]
        
        if not indices_to_delete:
            print(f"⚠️ '{name_to_delete}' 이름으로 등록된 데이터가 없습니다.")
            return

        # 3. 데이터 삭제 (뒤에서부터 삭제해야 인덱스가 꼬이지 않습니다)
        new_W = []
        new_labels = []
        
        for i in range(initial_count):
            if i not in indices_to_delete:
                new_W.append(W[i])
                new_labels.append(labels[i])
                
        deleted_count = initial_count - len(new_labels)

        # 4. 수정된 데이터 저장
        new_data = {'W': new_W, 'labels': new_labels}
        
        with open(DB_FILE, 'wb') as f:
            pickle.dump(new_data, f)
            
        print(f"✅ '{name_to_delete}'에 대한 데이터 {deleted_count}개가 성공적으로 삭제되었습니다.")
        print(f"   현재 등록된 총 얼굴 수: {len(new_labels)}명")
        
    except Exception as e:
        print(f"❌ 데이터 삭제 및 저장 중 오류 발생: {e}")

# ==========================================================
# 🚀 실행 예시
# ==========================================================

# 1. 현재 등록된 데이터 확인
if os.path.exists(DB_FILE):
    with open(DB_FILE, 'rb') as f:
        data = pickle.load(f)
    print("--- 📋 현재 등록된 데이터 ---")
    print(f"총 등록된 얼굴 수: {len(data['labels'])}")
    print("등록된 이름 목록:", data['labels'])
    print("-----------------------------")
else:
    print("art_brain.pkl 파일이 존재하지 않습니다. 학습된 얼굴 데이터가 없습니다.")

# 2. 삭제할 이름 입력받기
name_to_delete = input("\n삭제를 원하는 사용자 이름을 입력하세요 (예: 홍길동): ")

# 3. 삭제 함수 실행
delete_face_data(name_to_delete)

# 4. 삭제 후 최종 데이터 확인 (선택 사항)
if os.path.exists(DB_FILE):
    with open(DB_FILE, 'rb') as f:
        data = pickle.load(f)
    print("\n--- ✅ 삭제 후 등록된 데이터 ---")
    print(f"총 등록된 얼굴 수: {len(data['labels'])}")
    print("등록된 이름 목록:", data['labels'])
    print("---------------------------------")