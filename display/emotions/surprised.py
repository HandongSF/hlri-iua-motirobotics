# emotions/surprised.py (수정됨)

import pygame
import math
from ..common_helpers import *

class Emotion:
    def __init__(self):
        # 애니메이션 상태를 클래스 내에서 관리
        self.animation_start_time = 0
        self.is_animating = False

    def reset(self):
        """이 감정이 다시 활성화될 때 애니메이션 상태를 초기화합니다."""
        self.animation_start_time = 0
        self.is_animating = False

    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']

        # 이 표정이 활성화될 때마다 애니메이션을 한 번만 실행
        if not self.is_animating:
            self.is_animating = True
            self.animation_start_time = time

        body_offset_y = 0
        jump_height = 35 # 점프 높이 증가
        animation_duration = 600 # 애니메이션 지속 시간

        elapsed = time - self.animation_start_time
        if elapsed < animation_duration:
            # 포물선 형태의 점프 애니메이션 (더 자연스러운 움직임)
            progress = elapsed / animation_duration
            body_offset_y = -jump_height * 4 * (progress - progress**2)
        else:
             # 애니메이션이 끝나면 is_animating을 False로 만들어 다른 표정으로 갔다가 돌아왔을 때 다시 애니메이션을 볼 수 있게 함
             # 단, main.py에서 상태가 바뀔 때 reset을 호출해주는 로직이 없으므로, 현재는 한 번만 실행됨.
             # 이 부분은 추후 개선 가능성이 있음.
             pass

        # --- 눈썹 (더 높이 올림) ---
        eyebrow_thickness = 12
        brow_y_offset = -115 + body_offset_y # 기존 -90보다 더 높게
        for i in [-1, 1]:
            eye_x = (left_eye[0] if i == -1 else right_eye[0])
            rect = pygame.Rect(eye_x - 40, brow_y_offset - 20, 80, 40)
            pygame.draw.arc(surface, WHITE, rect, math.radians(20), math.radians(160), eyebrow_thickness)
        
        # --- 입 모양 (세로로 더 길게) ---
        mouth_center_x = surface.get_width() // 2
        mouth_center_y = surface.get_height() // 2 + 120 + body_offset_y
        
        mouth_width = 90
        mouth_height = 120 # 기존 80에서 높이 증가
        
        pygame.draw.ellipse(surface, WHITE, (mouth_center_x - mouth_width // 2, mouth_center_y - mouth_height // 2, mouth_width, mouth_height))
        inner_mouth_width = mouth_width - 15
        inner_mouth_height = mouth_height - 15
        pygame.draw.ellipse(surface, DARK_GRAY, (mouth_center_x - inner_mouth_width // 2, mouth_center_y - inner_mouth_height // 2, inner_mouth_width, inner_mouth_height))

        # --- 눈 그리기 (더 커진 눈) ---
        pupil_radius = 60 # 기존 45에서 크기 대폭 증가
        
        draw_base_eye(surface, 
                      (left_eye[0], left_eye[1] + body_offset_y), 
                      offset, pupil_radius, START_BLUE, END_BLUE)
        
        draw_base_eye(surface, 
                      (right_eye[0], right_eye[1] + body_offset_y), 
                      offset, pupil_radius, START_BLUE, END_BLUE)