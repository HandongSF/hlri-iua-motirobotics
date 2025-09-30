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

# emotions/neutral.py
import pygame, math
from ..common_helpers import *
import random

class Emotion:
    def __init__(self):
        self.start_time = -1
        self.is_animating = True
        
        self.arc_pause_duration = 500
        self.left_eye_open_duration = 700
        self.right_eye_open1_duration = 400
        self.right_eye_pause_duration = 800
        self.right_eye_open2_duration = 400
        
        total_right_duration = self.right_eye_open1_duration + self.right_eye_pause_duration + self.right_eye_open2_duration
        self.total_duration = self.arc_pause_duration + total_right_duration

    def reset(self):
        """감정이 변경될 때마다 애니메이션을 리셋합니다."""
        self.start_time = -1
        self.is_animating = True

    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = \
            common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        
        if self.start_time == -1:
            self.start_time = time

        elapsed = time - self.start_time
        
        if self.is_animating and elapsed >= self.total_duration:
            self.is_animating = False

        ## 1. 시작 모양 표시 단계 (0 ~ 0.5초)
        if elapsed < self.arc_pause_duration:
            for eye_center in [left_eye, right_eye]:
                # 눈 영역을 검은색으로 먼저 칠합니다.
                pygame.draw.rect(surface, (0, 0, 0), (eye_center[0] - 100, eye_center[1] - 100, 200, 210))
                
                ## ▼▼▼▼▼▼▼▼▼▼ 여기가 핵심 수정 부분 ▼▼▼▼▼▼▼▼▼▼
                # 아크 대신 가로로 긴 흰색 사각형을 그립니다.
                line_width = 150
                line_height = 10
                line_rect = pygame.Rect(eye_center[0] - line_width // 2, eye_center[1] - line_height // 2, line_width, line_height)
                pygame.draw.rect(surface, WHITE, line_rect)
                ## ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
        
        ## 2. 눈 뜨기 애니메이션 단계
        else:
            elapsed_after_arc = elapsed - self.arc_pause_duration
            
            # --- 왼쪽 눈 그리기 (바로 뜨기) ---
            progress_left = min(elapsed_after_arc / self.left_eye_open_duration, 1.0)
            lid_height_left = 100 * (1 - progress_left)
            
            draw_base_eye(surface, left_eye, offset, 35, START_BLUE, END_BLUE)
            top_lid_rect_left = pygame.Rect(left_eye[0] - 100, left_eye[1] - 100, 200, lid_height_left)
            pygame.draw.rect(surface, (0, 0, 0), top_lid_rect_left)
            bottom_lid_rect_left = pygame.Rect(left_eye[0] - 100, left_eye[1] + 100 - lid_height_left, 200, lid_height_left + 5)
            pygame.draw.rect(surface, (0, 0, 0), bottom_lid_rect_left)

            # --- 오른쪽 눈 그리기 (단계별로 뜨기) ---
            lid_height_right = 0

            if elapsed_after_arc < self.right_eye_open1_duration:
                progress = elapsed_after_arc / self.right_eye_open1_duration
                lid_height_right = 100 - (50 * progress)
            
            elif elapsed_after_arc < self.right_eye_open1_duration + self.right_eye_pause_duration:
                lid_height_right = 50
            
            else:
                elapsed_part2 = elapsed_after_arc - (self.right_eye_open1_duration + self.right_eye_pause_duration)
                progress = min(elapsed_part2 / self.right_eye_open2_duration, 1.0)
                lid_height_right = 50 - (50 * progress)

            draw_base_eye(surface, right_eye, offset, 35, START_BLUE, END_BLUE)
            top_lid_rect_right = pygame.Rect(right_eye[0] - 100, right_eye[1] - 100, 200, lid_height_right)
            pygame.draw.rect(surface, (0, 0, 0), top_lid_rect_right)
            bottom_lid_rect_right = pygame.Rect(right_eye[0] - 100, right_eye[1] + 100 - lid_height_right, 200, lid_height_right + 5)
            pygame.draw.rect(surface, (0, 0, 0), bottom_lid_rect_right)

        # 입 모양은 항상 그립니다.
        pygame.draw.arc(surface, WHITE, (surface.get_width()//2-40, surface.get_height()//2+120, 80, 40), math.pi, 2*math.pi, 5)