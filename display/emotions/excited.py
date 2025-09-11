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

# emotions/excited.py (바운싱 효과 제거)
import pygame, math
from ..common_helpers import *

class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        
        # [수정] 바운싱 효과 제거: body_offset을 (0,0)으로 고정
        body_offset = (0, 0) 

        # 입 모양 (이전과 동일)
        mouth_rect = pygame.Rect(surface.get_width()//2-80, surface.get_height()//2+100+body_offset[1], 160, 80)
        pygame.draw.arc(surface, WHITE, mouth_rect, math.pi, 2*math.pi, 10)
        pygame.draw.line(surface, WHITE, (mouth_rect.left, mouth_rect.centery), (mouth_rect.right, mouth_rect.centery), 10)

        # 눈 그리기 (이전과 동일, 별 하이라이트 유지)
        draw_base_eye(surface, 
                      (left_eye[0]+body_offset[0], left_eye[1]+body_offset[1]), 
                      offset, 55, START_BLUE, END_BLUE, is_excited=True)
        draw_base_eye(surface, 
                      (right_eye[0]+body_offset[0], right_eye[1]+body_offset[1]), 
                      offset, 55, START_BLUE, END_BLUE, is_excited=True)