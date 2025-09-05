# emotions/neutral.py
import pygame, math
from ..common_helpers import *

class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset = common_data['left_eye'], common_data['right_eye'], common_data['offset']
        draw_base_eye(surface, left_eye, offset, 35, START_BLUE, END_BLUE)
        draw_base_eye(surface, right_eye, offset, 35, START_BLUE, END_BLUE)
        pygame.draw.arc(surface, WHITE, (surface.get_width()//2-40, surface.get_height()//2+120, 80, 40), math.pi, 2*math.pi, 5)