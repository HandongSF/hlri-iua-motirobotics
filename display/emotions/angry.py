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

# emotions/angry.py
import pygame, math
from ..common_helpers import *

class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, _ = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        mouth_rect = (surface.get_width()//2-80, surface.get_height()//2+140, 160, 60)
        pygame.draw.arc(surface, WHITE, mouth_rect, 0, math.pi, 8)
        draw_base_eye(surface, left_eye, offset, 35, (150,0,0), RED)
        draw_base_eye(surface, right_eye, offset, 35, (150,0,0), RED)