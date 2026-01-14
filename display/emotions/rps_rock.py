import pygame
import os

class Emotion:
    def __init__(self):
        # 1. 이미지 파일 경로 설정
        # 현재 파일(rps_rock.py)의 위치를 기준으로 images 폴더를 찾습니다.
        base_path = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(base_path, "images", "rock.png")
        
        self.image = None
        try:
            # 2. 이미지 로드 (convert_alpha는 투명 배경 처리에 필수)
            img = pygame.image.load(image_path)
            
            # 3. 크기 조절 (원하는 크기로 조절하세요. 예: 300x300)
            # 로봇 화면이 800x480 이므로 높이 300 정도면 적당합니다.
            scale_size = (300, 300) 
            self.image = pygame.transform.scale(img, scale_size)
            
        except Exception as e:
            print(f"❌ [RPS] 바위 이미지 로드 실패: {e}")

    def draw(self, surface, common_data):
        if self.image:
            # 4. 화면 정중앙에 배치
            # surface.get_rect().center는 화면의 중심 좌표를 줍니다.
            img_rect = self.image.get_rect(center=(surface.get_width() // 2, surface.get_height() // 2))
            
            # 5. 그리기 (Blit)
            surface.blit(self.image, img_rect)
        else:
            # 이미지가 없을 경우 비상용 텍스트 출력
            font = pygame.font.SysFont("arial", 50)
            text = font.render("ROCK (No Image)", True, (255, 255, 255))
            rect = text.get_rect(center=(surface.get_width() // 2, surface.get_height() // 2))
            surface.blit(text, rect)