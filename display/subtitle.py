# ============================================================
#Licensed to the Apache Software Foundation (ASF) under one
#or more contributor license agreements.  See the NOTICE file
#distributed with this work for additional information
#regarding copyright ownership.  The ASF licenses this file
#to you under the Apache License, Version 2.0 (the
#"License"); you may not use this file except in compliance
#with the License.  You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
# ============================================================

# subtitle.py

import tkinter as tk
from queue import Empty
import multiprocessing

def subtitle_window_process(subtitle_q: multiprocessing.Queue):
    """자막을 표시하는 별도의 Tkinter 창을 관리하는 프로세스 함수"""
    try:
        root = tk.Tk()
        root.title("Moti Subtitle")

        # ... (창 크기 및 위치 설정 코드는 그대로) ...
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = 1100
        window_height = 500
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

        subtitle_label = tk.Label(
            root, text="", font=("Malgun Gothic", 50, "bold"),
            fg="white", bg="black", wraplength=window_width - 20
        )
        subtitle_label.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        # [추가 1] 타이머 ID를 저장할 변수 생성
        clear_timer_id = None

        def check_queue():
            """큐를 주기적으로 확인하여 라벨의 텍스트를 업데이트"""
            nonlocal clear_timer_id # [추가 2] 중첩 함수 내에서 상위 변수를 수정하기 위해 nonlocal 선언
            try:
                message = subtitle_q.get_nowait()
                if message == "__QUIT__":
                    root.destroy()
                    return
                
                # [수정 1] 새로운 메시지를 받으면, 기존에 설정된 '자막 지우기' 예약을 취소
                if clear_timer_id:
                    root.after_cancel(clear_timer_id)

                subtitle_label.config(text=message)
                
                # [수정 2] 자막 길이에 따라 표시 시간을 동적으로 계산
                # 기본 2초 + 글자당 150ms (0.15초) 추가 (이 값은 조절 가능)
                base_duration = 2000 
                duration_per_char = 150
                display_duration_ms = base_duration + (len(message) * duration_per_char)

                # [수정 3] 계산된 시간 후에 자막을 지우도록 예약하고, 새로운 타이머 ID를 저장
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