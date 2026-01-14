import pygame
import os

class Emotion:
    def __init__(self):
        base_path = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(base_path, "images", "scissors.png") # <-- 여기 변경
        
        self.image = None
        try:
            img = pygame.image.load(image_path)
            self.image = pygame.transform.scale(img, (300, 300)) 
        except Exception as e:
            print(f"❌ [RPS] 가위 이미지 로드 실패: {e}")

    def draw(self, surface, common_data):
        if self.image:
            img_rect = self.image.get_rect(center=(surface.get_width() // 2, surface.get_height() // 2))
            surface.blit(self.image, img_rect)