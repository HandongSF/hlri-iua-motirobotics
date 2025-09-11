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

# mk2/wheel.py
import time, threading
from typing import Tuple, Set
from pynput import keyboard
from dynamixel_sdk import PortHandler, PacketHandler
from . import config as C, dxl_io as io

_pressed: Set[str] = set()

def compute_cmd() -> Tuple[int, int]:
    # 우선순위: W > S > A > D
    if 'w' in _pressed:
        return (C.LEFT_DIR * C.BASE_SPEED_UNITS,  C.RIGHT_DIR * C.BASE_SPEED_UNITS)
    if 's' in _pressed:
        return (-C.LEFT_DIR * C.BASE_SPEED_UNITS, -C.RIGHT_DIR * C.BASE_SPEED_UNITS)
    if 'a' in _pressed:
        return (-C.LEFT_DIR * C.TURN_SPEED_UNITS,  C.RIGHT_DIR * C.TURN_SPEED_UNITS)
    if 'd' in _pressed:
        return ( C.LEFT_DIR * C.TURN_SPEED_UNITS, -C.RIGHT_DIR * C.TURN_SPEED_UNITS)
    return (0, 0)

def set_wheel_speed(pkt: PacketHandler, port: PortHandler, lock, dxl_id: int, spd_signed: int):
    spd = int(io.clamp(spd_signed, C.VEL_MIN, C.VEL_MAX))
    with lock:
        io.write4s(pkt, port, dxl_id, C.ADDR_GOAL_VELOCITY, spd)

def wheel_loop(port: PortHandler, pkt: PacketHandler, lock, stop_event: threading.Event):
    def on_press(key):
        try:
            k = key.char.lower()
        except Exception:
            if key == keyboard.Key.esc:
                stop_event.set()
            return
        if k in ('w', 'a', 's', 'd'):
            _pressed.add(k)
        elif k == 'q':
            stop_event.set()

    def on_release(key):
        try:
            k = key.char.lower()
        except Exception:
            return
        if k in _pressed:
            _pressed.remove(k)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    print("▶ Wheel: W/A/S/D 누르는 동안만 동작, Q/ESC 종료")
    left_prev, right_prev = None, None

    try:
        while not stop_event.is_set():
            lcmd, rcmd = compute_cmd()
            if (lcmd, rcmd) != (left_prev, right_prev):
                set_wheel_speed(pkt, port, lock, C.LEFT_ID,  lcmd)
                set_wheel_speed(pkt, port, lock, C.RIGHT_ID, rcmd)
                left_prev, right_prev = lcmd, rcmd
            time.sleep(0.01)
    finally:
        try:
            set_wheel_speed(pkt, port, lock, C.LEFT_ID,  0)
            set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
            with lock:
                io.write1(pkt, port, C.LEFT_ID,  C.ADDR_TORQUE_ENABLE, 0)
                io.write1(pkt, port, C.RIGHT_ID, C.ADDR_TORQUE_ENABLE, 0)
        finally:
            listener.stop()
        print("■ Wheel loop 종료")
