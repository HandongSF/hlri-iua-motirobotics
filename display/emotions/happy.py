# emotions/happy.py
import pygame, math
from ..common_helpers import *

class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        bounce = math.sin(time * 0.01) * 10
        body_offset = (0, bounce)
        mouth_rect = (surface.get_width()//2-100, surface.get_height()//2+80+bounce, 200, 100)
        pygame.draw.arc(surface, WHITE, mouth_rect, math.pi, 2*math.pi, 15)
        draw_base_eye(surface, (left_eye[0]+body_offset[0], left_eye[1]+body_offset[1]), offset, 60, START_BLUE, END_BLUE, highlight_r=30)
        draw_base_eye(surface, (right_eye[0]+body_offset[0], right_eye[1]+body_offset[1]), offset, 60, START_BLUE, END_BLUE, highlight_r=30)