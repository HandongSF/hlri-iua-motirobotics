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

import pygame
import math
from ..common_helpers import *

def draw_happy_cheeks(surface, common_data):
    left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']

    # Create a scaled cheek surface
    cheek = pygame.Surface((100,50), pygame.SRCALPHA)
    
    alpha = 150 + math.sin(time * 0.005) * 50
    pygame.draw.ellipse(cheek, PINK + (int(alpha),), (0,0,100,50))
    
    # Unscaled coordinates
    surface.blit(cheek, (left_eye[0]-150, left_eye[1]+20))
    surface.blit(cheek, (right_eye[0]+50, right_eye[1]+20))


def draw_tender_cheeks(surface, common_data):
    left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
    
    # Create a scaled cheek surface
    cheek = pygame.Surface((100,50), pygame.SRCALPHA)
    
    alpha = 100 + math.sin(time * 0.002) * 50
    pygame.draw.ellipse(cheek, PINK + (int(alpha),), (0,0,100,50))
    
    # Unscaled coordinates
    surface.blit(cheek, (left_eye[0]-150, left_eye[1]+20))
    surface.blit(cheek, (right_eye[0]+50, right_eye[1]+20))