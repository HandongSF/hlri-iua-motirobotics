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
# emotions/scanning.py

import pygame
import math
from ..common_helpers import *

# 이 감정에서만 사용하는 전용 상수
BEAM_COLOR = (200, 255, 255)

def draw_flashlight_beam(surface, start_pos, offset_vector, beam_length=600, beam_width_angle=40):
    """
    눈에서 뻗어나가는 빛을 그립니다.
    """
    # 1. 투명한 표면 생성
    beam_surface = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    
    # 2. 각도 계산 (시선 방향)
    angle = math.atan2(offset_vector[1], offset_vector[0])
    
    # 3. 부채꼴(삼각형) 모양 좌표 계산
    half_angle = math.radians(beam_width_angle / 2)
    
    # 왼쪽 끝점
    x1 = start_pos[0] + beam_length * math.cos(angle - half_angle)
    y1 = start_pos[1] + beam_length * math.sin(angle - half_angle)
    
    # 오른쪽 끝점
    x2 = start_pos[0] + beam_length * math.cos(angle + half_angle)
    y2 = start_pos[1] + beam_length * math.sin(angle + half_angle)
    
    points = [start_pos, (x1, y1), (x2, y2)]
    
    # 4. 빛 그리기
    # 바깥쪽 (넓고 투명함)
    pygame.draw.polygon(beam_surface, (*BEAM_COLOR, 150), points) 
    
    # 안쪽 (좁고 조금 더 진함 - 코어)
    core_half_angle = half_angle * 0.5
    cx1 = start_pos[0] + beam_length * math.cos(angle - core_half_angle)
    cy1 = start_pos[1] + beam_length * math.sin(angle - core_half_angle)
    cx2 = start_pos[0] + beam_length * math.cos(angle + core_half_angle)
    cy2 = start_pos[1] + beam_length * math.sin(angle + core_half_angle)
    core_points = [start_pos, (cx1, cy1), (cx2, cy2)]
    
    pygame.draw.polygon(beam_surface, (*BEAM_COLOR, 225), core_points)

    return beam_surface


class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        
        # --- 입 모양 (짧은 Rect) ---
        mouth_center_x = surface.get_width() // 2
        mouth_width = 50
        mouth_height = 8 
        mouth_y = surface.get_height() // 2 + 140 
        
        mouth_rect = pygame.Rect(mouth_center_x - mouth_width // 2, mouth_y, mouth_width, mouth_height)
        pygame.draw.rect(surface, WHITE, mouth_rect)

        # --- 눈 그리기 ---
        pupil_radius = 35
        
        # 왼쪽 눈 그리기 & 동공 위치 받기 (common_helpers의 함수 사용 가정)
        # 참고: draw_base_eye가 None을 반환하더라도 눈 깜빡임을 무시해야 합니다.
        left_pupil_center = draw_base_eye(surface, left_eye, offset, pupil_radius, START_BLUE, END_BLUE)
        
        # 오른쪽 눈 그리기 & 동공 위치 받기
        right_pupil_center = draw_base_eye(surface, right_eye, offset, pupil_radius, START_BLUE, END_BLUE)

        # --- 플래시라이트 효과 ---
        # offset 벡터가 회전하므로 빛도 같이 회전합니다.
        beam_vector = offset
        
        # 벡터 크기가 너무 작을 경우(중앙)에 대한 예외처리
        if abs(offset[0]) < 1 and abs(offset[1]) < 1:
             beam_vector = (0, 20) 

        combined_beam_surface = pygame.Surface(surface.get_size(), pygame.SRCALPHA)

        # 동공 위치가 유효한지 확인 후 그리기 (이전 오류 해결)
        if left_pupil_center is not None:
        # draw_flashlight_beam이 이제 surface가 아닌 beam_surface를 반환하도록 가정
            left_beam = draw_flashlight_beam(surface, left_pupil_center, beam_vector)
            combined_beam_surface.blit(left_beam, (0, 0)) # 임시 표면에 합성

        if right_pupil_center is not None:
            right_beam = draw_flashlight_beam(surface, right_pupil_center, beam_vector)
            combined_beam_surface.blit(right_beam, (0, 0)) # 임시 표면에 합성
            
        # 모든 빔이 그려진 후, 최종적으로 메인 화면에 합성 (눈을 덮지 않도록)
        surface.blit(combined_beam_surface, (0, 0))