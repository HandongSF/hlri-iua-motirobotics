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
