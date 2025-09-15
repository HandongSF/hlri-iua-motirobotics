# ============================================================
#Licensed to the Apache Software Foundation (ASF) under one
#or more contributor license agreements.  See the NOTICE file
#distributed with this work for additional information
#regarding copyright ownership.  The ASF licenses this file
#to you under a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
# ============================================================


# emotions/sleepy.py (새로운 입, 침 추가 최종본)

import pygame
import math
import random
from ..common_helpers import *

# [추가] 입 채우기 색상 정의
MOUTH_FILL_COLOR = (255, 255, 255, 150) # 반투명한 흰색

class Emotion:
    def __init__(self):
        # Zzz 애니메이션 관리
        self.z_particles = []
        self.next_z_time = 0
        try:
            self.z_font = pygame.font.SysFont('Arial', 40, bold=True)
        except:
            self.z_font = pygame.font.Font(None, 50)

    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']

        # --- 애니메이션 ---
        # 느리게 숨 쉬는 듯한 기본 움직임
        breathing_offset = math.sin(time * 0.0008) * 4
        body_offset = (0, breathing_offset)

        # --- Zzz 애니메이션 ---
        if time > self.next_z_time:
            eye_top_x = surface.get_width() // 2 + 100
            eye_top_y = surface.get_height() // 2 - 80
            self.z_particles.append({
                'pos': [eye_top_x, eye_top_y + body_offset[1]],
                'size': random.randint(30, 50),
                'alpha': 255,
                'x_drift': random.uniform(0.1, 0.4),
                'char': "z"
            })
            self.next_z_time = time + random.randint(1500, 2500)

        for p in self.z_particles[:]:
            p['pos'][1] -= 0.7
            p['pos'][0] += p['x_drift']
            p['alpha'] -= 1.2
            if p['alpha'] <= 0:
                self.z_particles.remove(p)
            else:
                z_text = self.z_font.render(p['char'], True, WHITE)
                z_text = pygame.transform.scale(z_text, (p['size'], p['size']))
                z_text.set_alpha(p['alpha'])
                surface.blit(z_text, p['pos'])

        # --- [수정] 입 모양 및 침 추가 ---
        mouth_center_x = surface.get_width() // 2
        mouth_y = surface.get_height() // 2 + 135 + body_offset[1]
        
        # 1. 입 모양 그리기 (반원 모양)
        mouth_rect = pygame.Rect(mouth_center_x - 50, mouth_y, 100, 70)
        
        # 반투명 효과를 위해 별도의 Surface 사용
        mouth_surface = pygame.Surface(mouth_rect.size, pygame.SRCALPHA)
        # 아래쪽 절반에 해당하는 타원을 그림
        pygame.draw.ellipse(mouth_surface, MOUTH_FILL_COLOR, (0, 0, mouth_rect.width, mouth_rect.height))
        # 윗부분을 잘라내기 위해 투명한 사각형을 덮음
        pygame.draw.rect(mouth_surface, (0, 0, 0, 0), (0, 0, mouth_rect.width, mouth_rect.height / 2))
        
        # 혀 그리기 (입 안쪽에 작은 핑크색 타원)
        tongue_rect = pygame.Rect(mouth_rect.width * 0.2, mouth_rect.height * 0.1, mouth_rect.width * 0.6, mouth_rect.height * 0.5)
        pygame.draw.ellipse(mouth_surface, PINK, tongue_rect)
        
        surface.blit(mouth_surface, mouth_rect.topleft)

        # 2. 침 그리기
        drool_start_x = mouth_rect.right - 25
        drool_start_y = mouth_rect.bottom - 20
        drool_length = 30
        
        # 침 방울 부분 (동그라미)
        pygame.draw.circle(surface, SKY_BLUE_START, (drool_start_x, drool_start_y + drool_length), 10)
        # 침 흐르는 부분 (선)
        pygame.draw.line(surface, SKY_BLUE_START, (drool_start_x, drool_start_y), (drool_start_x, drool_start_y + drool_length), 5)
        

        # --- 눈 (웃는 듯이 감은 눈) ---
        for i in [-1, 1]:
            eye_center = (left_eye if i == -1 else right_eye)
            eye_x = eye_center[0]
            eye_y = eye_center[1] + body_offset[1]
            arc_width = 150
            arc_height = 100
            arc_rect = pygame.Rect(eye_x - arc_width // 2, eye_y - arc_height // 2 + 10, arc_width, arc_height)
            pygame.draw.arc(surface, WHITE, arc_rect, math.pi, 2 * math.pi, 15)