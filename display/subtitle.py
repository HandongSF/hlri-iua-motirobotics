import tkinter as tk
from queue import Empty
import multiprocessing

def subtitle_window_process(subtitle_q: multiprocessing.Queue):
    """자막을 표시하는 별도의 Tkinter 창을 관리하는 프로세스 함수"""
    try:
        root = tk.Tk()
        root.title("Moti Subtitle")

        # 화면 크기를 얻어와 창 위치를 하단 중앙으로 설정
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = 800
        window_height = 100
        x_pos = (screen_width // 2) - (window_width // 2)
        y_pos = screen_height - window_height - 50 # 화면 하단에서 50px 위
        root.geometry(f"{window_width}x{window_height}+{x_pos}+{y_pos}")

        # 창 테두리 없애기, 항상 위에 있도록 설정
        root.configure(bg="black")
        root.wm_attributes("-topmost", 1)
        # 🔻🔻🔻 [수정된 부분 1] "자막" 제목 라벨 추가 🔻🔻🔻
        # 제목을 표시할 작은 라벨을 만들어 창의 상단 왼쪽에 배치합니다.
        title_label = tk.Label(
            root,
            text="",
            font=("Malgun Gothic", 30), # 제목 폰트는 약간 작게 설정
            fg="#AAAAAA", # 제목 글자색은 약간 회색으로 하여 본문과 구분
            bg="black"
        )
        # anchor='w'는 виджет을 서쪽(west), 즉 왼쪽에 정렬하라는 의미입니다.
        title_label.pack(anchor='w', padx=10, pady=(5, 0))

        # 🔻🔻🔻 [수정된 부분 2] 기존 라벨 변수명 변경 및 패딩 조절 🔻🔻🔻
        # 실제 자막 내용을 표시할 라벨 위젯 (기존 'label' -> 'subtitle_label')
        subtitle_label = tk.Label(
            root, 
            text="", 
            font=("Malgun Gothic", 50, "bold"), # 폰트 설정
            fg="white",      # 글자색
            bg="black",      # 배경색
            wraplength=window_width - 20 # 창 너비에 맞춰 자동 줄 바꿈
        )
        # 제목 라벨 아래 공간을 모두 채우도록 설정하고, 위쪽 패딩을 줄여 제목에 가깝게 붙입니다.
        subtitle_label.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        def check_queue():
            """큐를 주기적으로 확인하여 라벨의 텍스트를 업데이트"""
            try:
                # 큐에서 메시지를 비동기적으로 가져옴
                message = subtitle_q.get_nowait()
                if message == "__QUIT__":
                    root.destroy()
                    return
                
                # 🔻🔻🔻 [수정된 부분 3] 업데이트할 라벨을 subtitle_label로 지정 🔻🔻🔻
                subtitle_label.config(text=message)
                
                # 7초 후에 자막을 지우도록 예약
                root.after(7000, lambda: subtitle_label.config(text=""))

            except Empty:
                pass # 큐가 비어있으면 아무것도 하지 않음
            
            # 100ms마다 이 함수를 다시 실행
            root.after(100, check_queue)

        print("💬 자막 창 프로세스 시작됨.")
        check_queue() # 큐 확인 루프 시작
        root.mainloop() # Tkinter 이벤트 루프 시작

    except Exception as e:
        print(f"❌ 자막 창 프로세스 오류: {e}")
    finally:
        print("🛑 자막 창 프로세스 종료됨.")