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

# emotions/thinking.py

import pygame
import math
from ..common_helpers import *

class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        
        RIGHT_SIDE_RAISE = 8  
        
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
        
        draw_base_eye(surface, left_eye, offset, pupil_radius, START_BLUE, END_BLUE)
        draw_base_eye(surface, (right_eye[0], right_eye[1] - RIGHT_SIDE_RAISE), offset, pupil_radius, START_BLUE, END_BLUE)

        
        # 오른쪽 상단에 디테일한 세그먼트 로딩 스피너 ---
        spinner_center_x = surface.get_width() - 50 
        spinner_center_y = 50                      
        spinner_radius = 25
        segment_thickness = 4
        
        num_segments = 12         # 세그먼트 개수 (12개 조각)
        rotation_speed = 0.01   # 회전 속도
        gap_angle = math.pi / 40 # 세그먼트 사이의 간격 각도
        segment_angle = (2 * math.pi / num_segments) - gap_angle

        current_rotation = time * rotation_speed # 시간에 따른 현재 회전 각도

        # 색상 정의 (어두운 회색 -> 밝은 흰색)
        DARK_COLOR = START_BLUE
        BRIGHT_COLOR = END_BLUE

        # 스피너를 그릴 사각형 영역
        spinner_rect = pygame.Rect(
            spinner_center_x - spinner_radius, 
            spinner_center_y - spinner_radius, 
            spinner_radius * 2, 
            spinner_radius * 2
        )

        for i in range(num_segments):
            # 각 세그먼트의 시작 각도 계산
            base_start_angle = (i * (2 * math.pi / num_segments)) 
            
            # 현재 회전 각도를 더하여 세그먼트가 회전하도록 함
            segment_start_angle = base_start_angle + current_rotation
            segment_end_angle = segment_start_angle + segment_angle

            # '밝은 부분이 도는' 효과를 위한 밝기 계산 (0.0 ~ 1.0)
            # `math.cos` 함수를 이용해 부드러운 밝기 변화를 만듭니다.
            brightness_factor = (math.cos((i * (2 * math.pi / num_segments)) - current_rotation) + 1) / 2
            
            # 흰색/회색 간 보간하여 세그먼트 색상 결정
            r = int(DARK_COLOR[0] + (BRIGHT_COLOR[0] - DARK_COLOR[0]) * brightness_factor)
            g = int(DARK_COLOR[1] + (BRIGHT_COLOR[1] - DARK_COLOR[1]) * brightness_factor)
            b = int(DARK_COLOR[2] + (BRIGHT_COLOR[2] - DARK_COLOR[2]) * brightness_factor)
            segment_color = (r, g, b)

            pygame.draw.arc(
                surface, 
                segment_color,
                spinner_rect,
                segment_start_angle, 
                segment_end_angle,
                segment_thickness
            )