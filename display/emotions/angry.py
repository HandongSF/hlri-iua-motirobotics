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