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

# mk2/init.py
from dynamixel_sdk import PortHandler, PacketHandler
from . import config as C, dxl_io as io

def _init_position_mode_ids(port: PortHandler, pkt: PacketHandler, lock, ids):
    with lock:
        for dxl_id in ids:
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 0)
            io.write1(pkt, port, dxl_id, C.ADDR_OPERATING_MODE, 3)  # Position
            io.write4(pkt, port, dxl_id, C.ADDR_PROFILE_VELOCITY, int(C.PROFILE_VELOCITY))
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 1)

def init_pan_tilt_and_extras(port: PortHandler, pkt: PacketHandler, lock):
    _init_position_mode_ids(port, pkt, lock, (C.PAN_ID, C.TILT_ID, *C.EXTRA_POS_IDS))

def init_wheels(port: PortHandler, pkt: PacketHandler, lock):
    with lock:
        for dxl_id in (C.LEFT_ID, C.RIGHT_ID):
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 0)
            io.write1(pkt, port, dxl_id, C.ADDR_OPERATING_MODE, 1)  # Velocity
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 1)

def stop_all_wheels(pkt: PacketHandler, port: PortHandler, lock):
    from . import wheel
    wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID,  0)
    wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
