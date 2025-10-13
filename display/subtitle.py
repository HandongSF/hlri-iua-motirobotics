# ============================================================
#Licensed to the Apache Software Foundation (ASF) under one
#or more contributor license agreements.  See the NOTICE file
#distributed with this work for additional information
#regarding copyright ownership.  The ASF licenses this file
#to you under the Apache License, Version 2.0 (the
#"License"); you may not use this file except in compliance
#with the License.  You may obtain a copy of the License at

#   http://www.apache.org/licenses/LICENSE-2.0

#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
# ============================================================

import tkinter as tk
from queue import Empty
import multiprocessing
try:
    import screeninfo
except ImportError:
    screeninfo = None

def subtitle_window_process(subtitle_q: multiprocessing.Queue):
    """자막을 표시하는 별도의 Tkinter 창을 관리하는 프로세스 함수"""
    try:
        root = tk.Tk()
        root.title("Moti Subtitle")

        # 모니터 개수에 따라 창 위치를 다르게 설정 ---
        # 모니터가 3개 이상일 경우, 자막 창을 띄울 모니터 번호
        # 0 = 1번(주) 모니터, 1 = 2번 모니터, 2 = 3번 모니터
        MONITOR_INDEX_FOR_TRIPLE_SETUP = 2

        window_width = 1280
        window_height = 250
        x_pos, y_pos = 0, 0 # 기본 위치

        if screeninfo:
            try:
                monitors = screeninfo.get_monitors()
                num_monitors = len(monitors)
                
                target_index = 0 # 기본값은 주 모니터(0번)

                # [수정된 로직] 모니터 개수에 따라 위치 결정
                if num_monitors >= 3:
                    # 모니터가 3개 이상이면 지정된 모니터 사용
                    target_index = MONITOR_INDEX_FOR_TRIPLE_SETUP
                    print(f"✅ 모니터가 {num_monitors}개 감지되어, 지정된 모니터 #{target_index}에 창을 배치합니다.")
                else:
                    # 모니터가 1개 또는 2개이면 주 모니터(0번) 사용
                    target_index = 0
                    print(f"✅ 모니터가 {num_monitors}개 감지되어, 주 모니터(#{target_index})에 창을 배치합니다.")

                # 설정한 모니터 번호가 유효한지 최종 확인
                if num_monitors > target_index:
                    target_monitor = monitors[target_index]
                else:
                    # 유효하지 않으면 주 모니터를 최후의 보루로 사용
                    target_monitor = monitors[0]
                    print(f"⚠️ 지정된 모니터 #{target_index}를 찾을 수 없어 주 모니터에 배치합니다.")

                # 선택된 모니터를 기준으로 창 위치 계산 (가로 중앙, 하단)
                x_pos = target_monitor.x + (target_monitor.width - window_width) // 2
                y_pos = target_monitor.y + target_monitor.height - window_height - 50

            except Exception as e:
                print(f"❌ 모니터 정보 확인 중 오류 발생: {e}. 기본 위치를 사용합니다.")
                # screeninfo 실패 시 기존 로직으로 대체
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                x_pos = (screen_width // 2) - (window_width // 2)
                y_pos = screen_height - window_height - 50
        else:
            print("⚠️ 'screeninfo' 라이브러리가 없어 주 모니터에 배치합니다. (pip install screeninfo)")
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
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

