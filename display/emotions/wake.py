import pygame
import math
# from common_helpers import * # 이 부분은 보통 색상 정의 등을 담고 있습니다.
from . import neutral

# <<< 수정된 부분: WHITE 색상 정의 추가
WHITE = (255, 255, 255)

class Emotion:
    def __init__(self):
        self.is_animating = True
        self.start_time = 0
        self.duration_phase1 = 750
        self.duration_pause = 1000
        self.duration_phase2 = 750
        self.total_duration = self.duration_phase1 + self.duration_pause + self.duration_phase2

    def reset(self):
        self.is_animating = True
        self.start_time = pygame.time.get_ticks()

    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']

        neutral.Emotion().draw(surface, common_data)

        if self.is_animating:
            elapsed = time - self.start_time
            
            if elapsed < self.duration_phase1:
                progress = elapsed / self.duration_phase1 / 2
            elif elapsed < self.duration_phase1 + self.duration_pause:
                progress = 0.5
            else:
                phase2_elapsed = elapsed - (self.duration_phase1 + self.duration_pause)
                progress = 0.5 + (phase2_elapsed / self.duration_phase2) / 2
            
            progress = min(progress, 1.0)
            
            lid_height = 100 * (1 - progress)

            for eye_center in [left_eye, right_eye]:
                top_lid_rect = (eye_center[0] - 100, eye_center[1] - 100, 200, lid_height + 10)
                pygame.draw.rect(surface, (0, 0, 0), top_lid_rect)

                bottom_lid_rect = (eye_center[0] - 100, eye_center[1] + 100 - lid_height, 200, lid_height + 10)
                pygame.draw.rect(surface, (0, 0, 0), bottom_lid_rect)

            if elapsed >= self.total_duration:
                self.is_animating = False
        
        pygame.draw.arc(surface, WHITE, (surface.get_width() // 2 - 40, surface.get_height() // 2 + 120, 80, 40), math.pi, 2 * math.pi, 5)