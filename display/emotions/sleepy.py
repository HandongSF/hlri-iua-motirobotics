# emotions/sleepy.py (최종 수정본 - 눈 색상 변경)

import pygame
import math
import random
from ..common_helpers import *

class Emotion:
    def __init__(self):
        # 꾸벅 조는 애니메이션 상태 관리
        self.is_nodding_off = False
        self.nod_off_start_time = 0
        self.nod_off_duration = 600
        self.next_nod_off_time = pygame.time.get_ticks() + random.randint(3000, 8000)

        # Zzz 애니메이션 관리
        self.z_particles = []
        self.next_z_time = 0
        try:
            # 폰트가 없을 경우를 대비해 기본 폰트 사용
            self.z_font = pygame.font.SysFont('Arial', 40, bold=True)
        except:
            self.z_font = pygame.font.Font(None, 50)


    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']

        # --- 애니메이션 ---
        # 1. 느리게 숨 쉬는 듯한 기본 움직임
        breathing_offset = math.sin(time * 0.0008) * 4
        body_offset_y = breathing_offset

        # 2. 꾸벅 조는 애니메이션
        if not self.is_nodding_off and time > self.next_nod_off_time:
            self.is_nodding_off = True
            self.nod_off_start_time = time
            self.next_nod_off_time = time + random.randint(4000, 9000)

        if self.is_nodding_off:
            elapsed = time - self.nod_off_start_time
            if elapsed < self.nod_off_duration:
                progress = elapsed / self.nod_off_duration
                nod_y = math.sin(progress * math.pi) * 35
                body_offset_y += nod_y
            else:
                self.is_nodding_off = False

        body_offset = (0, body_offset_y)

        # --- Zzz 애니메이션 (위치 조정) ---
        if time > self.next_z_time:
            eye_top_x = surface.get_width() // 2 + 60
            eye_top_y = surface.get_height() // 2 - 100
            self.z_particles.append({
                'pos': [eye_top_x, eye_top_y + body_offset[1]],
                'size': random.randint(25, 45),
                'alpha': 255,
                'x_drift': random.uniform(-0.3, 0.3),
                'char': random.choice(["Z", "z"])
            })
            self.next_z_time = time + random.randint(1000, 2000)

        for p in self.z_particles[:]:
            p['pos'][1] -= 0.6
            p['pos'][0] += p['x_drift']
            p['alpha'] -= 1.0
            if p['alpha'] <= 0:
                self.z_particles.remove(p)
            else:
                z_text = self.z_font.render(p['char'], True, WHITE)
                z_text = pygame.transform.scale(z_text, (p['size'], p['size']))
                z_text.set_alpha(p['alpha'])
                surface.blit(z_text, p['pos'])

# --- 입 (침 자국 제거된 최종본) ---
        mouth_center_x = surface.get_width() // 2
        mouth_y = surface.get_height() // 2 + 130 + body_offset[1]
        
        # 입술의 두꺼운 검은 테두리
        mouth_outer_rect = pygame.Rect(mouth_center_x - 50, mouth_y - 10, 100, 70)
        pygame.draw.ellipse(surface, BLACK, mouth_outer_rect, 10)
        
        # 혀 모양 (흰색)
        tongue_rect_top = pygame.Rect(mouth_center_x - 35, mouth_y + 5, 70, 30)
        pygame.draw.ellipse(surface, WHITE, tongue_rect_top)
        pygame.draw.circle(surface, WHITE, (mouth_center_x, mouth_y + 40), 20)

        # 입 안쪽의 그림자
        pygame.draw.arc(surface, DARK_GRAY, (mouth_center_x - 40, mouth_y, 80, 50), 0, math.pi, 5)

        # 침 자국과 관련된 모든 코드를 삭제했습니다.


        # --- 눈 (이미지처럼 완전히 감긴 아치형 눈) ---
        for i in [-1, 1]:
            eye_center = (left_eye if i == -1 else right_eye)
            eye_x = eye_center[0] + body_offset[0]
            eye_y = eye_center[1] + body_offset[1]
            
            arc_width = 150
            arc_height = 100
            arc_rect = pygame.Rect(eye_x - arc_width // 2, eye_y - arc_height // 2 - 20, arc_width, arc_height)
            
            # [수정] 눈의 테두리 색상을 검은색(BLACK)에서 흰색(WHITE)으로 변경
            pygame.draw.arc(surface, WHITE, arc_rect, math.pi, 2 * math.pi, 15)