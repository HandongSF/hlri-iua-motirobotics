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