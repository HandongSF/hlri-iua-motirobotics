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

# emotions/sad.py (바운싱 효과 제거)
import pygame, math
from ..common_helpers import *

class Emotion:
    def __init__(self):
        self.tear_offset_y = 0
    
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        
        # [수정] 바운싱(slumping) 효과 제거
        body_offset = (0, 0) 
            
        mouth_rect = (surface.get_width()//2-80, surface.get_height()//2+130+body_offset[1], 160, 60)
        pygame.draw.arc(surface, WHITE, mouth_rect, 0, math.pi, 8)
        
        self.tear_offset_y = (self.tear_offset_y + 1.5) % 300
        if self.tear_offset_y < 200:
            tear_y = left_eye[1]-30+body_offset[1] + self.tear_offset_y
            size = 20 - (self.tear_offset_y/200)*10
            tear_surf = pygame.Surface((40,50), pygame.SRCALPHA)
            pygame.draw.circle(tear_surf, TEAR_COLOR, (20,20), size)
            pygame.draw.polygon(tear_surf, TEAR_COLOR, [(20-size,20), (20+size,20), (20,20-size*1.5)])
            surface.blit(tear_surf, (left_eye[0]-20, tear_y))
            
        draw_base_eye(surface, (left_eye[0]+body_offset[0], left_eye[1]+body_offset[1]), offset, 25, SAD_BLUE_START, SAD_BLUE_END)
        draw_base_eye(surface, (right_eye[0]+body_offset[0], right_eye[1]+body_offset[1]), offset, 25, SAD_BLUE_START, SAD_BLUE_END)