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

WHITE = (255, 255, 255)

def draw_angry_eyebrows(screen, common_data):
    left_eye = common_data['left_eye']
    right_eye = common_data['right_eye']
    
    # Left Eyebrow (unscaled coordinates)
    pygame.draw.line(screen, WHITE, 
                     (left_eye[0] + 100, left_eye[1] - 80),
                     (left_eye[0] - 10, left_eye[1] - 130), 15)
    
    # Right Eyebrow (unscaled coordinates)
    pygame.draw.line(screen, WHITE, 
                     (right_eye[0] - 100, right_eye[1] - 80),
                     (right_eye[0] + 10, right_eye[1] - 130), 15)


def draw_sad_eyebrows(screen, common_data):
    left_eye = common_data['left_eye']
    right_eye = common_data['right_eye']

    # Sad eyebrows (unscaled coordinates)
    pygame.draw.line(screen, WHITE, 
                     (left_eye[0] - 50, left_eye[1] - 80), 
                     (left_eye[0] + 50, left_eye[1] - 100), 10)
    
    pygame.draw.line(screen, WHITE, 
                     (right_eye[0] - 50, right_eye[1] - 80), 
                     (right_eye[0] + 50, right_eye[1] - 100), 10)


def draw_thinking_eyebrows(screen, common_data):
    left_eye = common_data['left_eye']
    right_eye = common_data['right_eye']
    
    eyebrow_thickness = 10
    RIGHT_SIDE_RAISE = 8

    # Left eyebrow (unscaled coordinates)
    left_brow_y = left_eye[1] - 90
    pygame.draw.line(screen, WHITE,
                     (left_eye[0] - 50, left_brow_y),
                     (left_eye[0] + 50, left_brow_y),
                     eyebrow_thickness)

    # Right eyebrow (unscaled coordinates)
    right_brow_y = right_eye[1] - 90 - RIGHT_SIDE_RAISE
    right_brow_rect = pygame.Rect(right_eye[0] - 50, right_brow_y - 30, 100, 60)
    pygame.draw.arc(screen, WHITE, right_brow_rect, math.radians(20), math.radians(160), eyebrow_thickness)