# emotions/scared.py
import pygame, math, random
from ..common_helpers import *

class Emotion:
    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']
        tremble = (random.randint(-5, 5), random.randint(-5, 5))
        mouth_points, mouth_y = [], surface.get_height()//2 + 140 + tremble[1]
        for i in range(120):
            angle = (i/120)*4*math.pi + time*0.01; y_off = math.sin(angle)*10
            mouth_points.append((surface.get_width()//2-60+i+tremble[0], mouth_y+y_off))
        pygame.draw.lines(surface, WHITE, False, mouth_points, 8)
        draw_base_eye(surface, (left_eye[0]+tremble[0], left_eye[1]+tremble[1]), offset, 15, START_BLUE, END_BLUE)
        draw_base_eye(surface, (right_eye[0]+tremble[0], right_eye[1]+tremble[1]), offset, 15, START_BLUE, END_BLUE)