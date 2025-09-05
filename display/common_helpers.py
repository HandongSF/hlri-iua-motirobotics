# common_helpers.py

import pygame
import math

# --- 색상 정의 ---
WHITE = (255, 255, 255)
PINK = (255, 182, 193)
YELLOW = (255, 255, 0)
RED = (200, 0, 0)
START_BLUE = (0, 150, 255)
END_BLUE = (100, 255, 255)
SKY_BLUE_START = (135, 206, 250)
SKY_BLUE_END = (176, 224, 230)
SAD_BLUE_START = (50, 50, 150)
SAD_BLUE_END = (100, 100, 200)
TEAR_COLOR = (100, 150, 255, 200)
DARK_GRAY = (30, 30, 30)
BLACK = (0, 0, 0)

# --- 그리기 헬퍼 함수 ---
def draw_gradient_pupil(surface, center, radius, start_color, end_color):
    if radius <= 0: return
    num_steps = int(radius)
    for i in range(num_steps):
        t = i / num_steps
        r = int(start_color[0] + (end_color[0] - start_color[0]) * t)
        g = int(start_color[1] + (end_color[1] - start_color[1]) * t)
        b = int(start_color[2] + (end_color[2] - start_color[2]) * t)
        pygame.draw.circle(surface, (r, g, b), center, radius - i)

def draw_star(surface, center, size, color):
    points = []
    for i in range(5):
        angle = math.radians(72 * i - 90)
        points.append((center[0] + size * math.cos(angle), center[1] + size * math.sin(angle)))
        angle_inner = math.radians(72 * i - 54)
        points.append((center[0] + (size/2.5) * math.cos(angle_inner), center[1] + (size/2.5) * math.sin(angle_inner)))
    pygame.draw.polygon(surface, color, points)

def draw_base_eye(surface, base_center, pupil_offset, pupil_radius, start_color, end_color, is_excited=False, highlight_r=20):
    pygame.draw.circle(surface, WHITE, base_center, 100)
    pupil_center = (int(base_center[0] + pupil_offset[0]), int(base_center[1] + pupil_offset[1]))
    pygame.draw.circle(surface, DARK_GRAY, pupil_center, 80)
    draw_gradient_pupil(surface, pupil_center, pupil_radius, start_color, end_color)
    highlight_pos = (pupil_center[0] - 30, pupil_center[1] - 30)
    if is_excited:
        star_size = 30 + math.sin(pygame.time.get_ticks() * 0.015) * 8
        draw_star(surface, highlight_pos, star_size, YELLOW)
    else:
        pygame.draw.circle(surface, WHITE, highlight_pos, highlight_r)