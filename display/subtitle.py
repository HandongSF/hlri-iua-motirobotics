import tkinter as tk
from queue import Empty
import multiprocessing

def subtitle_window_process(subtitle_q: multiprocessing.Queue):
    """자막을 표시하는 별도의 Tkinter 창을 관리하는 프로세스 함수"""
    try:
        root = tk.Tk()
        root.title("Moti Subtitle")

        # 창 크기 및 위치 설정
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = 1280
        window_height = 250
        x_pos = (screen_width // 2) - (window_width // 2)
        y_pos = screen_height - window_height - 50
        root.geometry(f"{window_width}x{window_height}+{x_pos}+{y_pos}")
        
        root.configure(bg="black")
        root.wm_attributes("-topmost", 1)
        
        title_label = tk.Label(
            root, text="", font=("Malgun Gothic", 30),
            fg="#AAAAAA", bg="black"
        )
        title_label.pack(anchor='w', padx=10, pady=(5, 0))

        # 폰트 목록
        SUBTITLE_FONT_NAMES = {
            'DEFAULT': "Malgun Gothic",
            'DINOSAUR': "학교안심 공룡알 OTF R",
            'KYOBO_2024': "교보 손글씨 2024 박서우",
            'ONGEUL': "온글잎 박다현체 Regular",
            'ROBOT': "엘리스 DX널리체 OTF Medium",
        }

        # 폰트 지정
        selected_font_name = SUBTITLE_FONT_NAMES['ONGEUL']


        subtitle_label = tk.Label(
            root, text="", font=(selected_font_name, 50, "bold"),
            fg="white", bg="black", wraplength=window_width - 20
        )
        subtitle_label.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        # 타이머 ID를 저장할 변수 생성
        clear_timer_id = None

        def check_queue():
            """큐를 주기적으로 확인하여 라벨의 텍스트를 업데이트"""
            nonlocal clear_timer_id
            try:
                message = subtitle_q.get_nowait()
                if message == "__QUIT__":
                    root.destroy()
                    return
                
                if clear_timer_id:
                    root.after_cancel(clear_timer_id)

                subtitle_label.config(text=message)
                
                # 자막 길이에 따라 표시 시간을 동적으로 계산
                base_duration = 2000 
                duration_per_char = 150
                display_duration_ms = base_duration + (len(message) * duration_per_char)

                # 계산된 시간 후에 자막을 지우도록 예약
                clear_timer_id = root.after(display_duration_ms, lambda: subtitle_label.config(text=""))

            except Empty:
                pass 
            
            root.after(100, check_queue)

        print("💬 자막 창 프로세스 시작됨.")
        check_queue() 
        root.mainloop() 

    except Exception as e:
        print(f"❌ 자막 창 프로세스 오류: {e}")
    finally:
        print("🛑 자막 창 프로세스 종료됨.")