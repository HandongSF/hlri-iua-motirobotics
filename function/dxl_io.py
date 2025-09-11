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

# mk2/dxl_io.py
from typing import Tuple
from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS
from . import config as C

def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v

def dxl_ok(comm_result: int, error: int) -> bool:
    return comm_result == COMM_SUCCESS and error == 0

def write1(pkt: PacketHandler, port: PortHandler, dxl_id: int, addr: int, val: int) -> bool:
    comm, err = pkt.write1ByteTxRx(port, dxl_id, addr, val)
    return dxl_ok(comm, err)

def write4(pkt: PacketHandler, port: PortHandler, dxl_id: int, addr: int, val: int) -> bool:
    comm, err = pkt.write4ByteTxRx(port, dxl_id, addr, val)
    return dxl_ok(comm, err)

def write4s(pkt: PacketHandler, port: PortHandler, dxl_id: int, addr: int, val_signed: int) -> bool:
    val = val_signed & 0xFFFFFFFF
    return write4(pkt, port, dxl_id, addr, val)

def read4(pkt: PacketHandler, port: PortHandler, dxl_id: int, addr: int) -> Tuple[int, int, int]:
    return pkt.read4ByteTxRx(port, dxl_id, addr)

def read_present_position(pkt: PacketHandler, port: PortHandler, lock, dxl_id: int) -> int:
    from .config import SERVO_MIN, SERVO_MAX
    with lock:
        pos, comm, err = read4(pkt, port, dxl_id, C.ADDR_PRESENT_POSITION)
    if comm == COMM_SUCCESS:
        return int(pos)
    return (SERVO_MIN + SERVO_MAX) // 2
