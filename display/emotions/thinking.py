# emotions/thinking.py (애니메이션 제거 및 고정)

import pygame
import math
from ..common_helpers import *

class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']

        # [수정] 애니메이션 효과를 제거하고 고정된 값으로 변경
        RIGHT_SIDE_RAISE = 8  # 오른쪽을 얼마나 올릴지 정하는 값
        
        # --- 입 모양 ('-' 모양, 중앙 고정) ---
        mouth_center_x = surface.get_width() // 2
        mouth_y = surface.get_height() // 2 + 130
        mouth_width = 80
        
        pygame.draw.line(surface, WHITE,
                         (mouth_center_x - mouth_width // 2, mouth_y),
                         (mouth_center_x + mouth_width // 2, mouth_y),
                         8)

        # --- 눈 그리기 (오른쪽 눈만 살짝 올림) ---
        pupil_radius = 35
        
        # 왼쪽 눈
        draw_base_eye(surface, left_eye, offset, pupil_radius, START_BLUE, END_BLUE)
        # 오른쪽 눈 (y좌표를 수정하여 살짝 올림)
        draw_base_eye(surface, (right_eye[0], right_eye[1] - RIGHT_SIDE_RAISE), offset, pupil_radius, START_BLUE, END_BLUE)