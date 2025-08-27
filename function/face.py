# mk2/face.py
import threading
from . import config as C, dxl_io as io, suppress
from dynamixel_sdk import PortHandler, PacketHandler

def face_tracker_worker(port: PortHandler, pkt: PacketHandler, lock: threading.Lock,
                        stop_event: threading.Event,
                        camera_index: int = 1,
                        draw_mesh: bool = True,
                        print_debug: bool = True):
    cv2, mp = suppress.import_cv2_mp()

    def read_pos(dxl_id: int) -> int:
        return io.read_present_position(pkt, port, lock, dxl_id)

    pan_pos  = read_pos(C.PAN_ID)
    tilt_pos = read_pos(C.TILT_ID)
    if print_debug:
        print(f"▶ Initial pan={pan_pos}, tilt={tilt_pos}")

    mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False, max_num_faces=1, refine_landmarks=True,
        min_detection_confidence=0.5, min_tracking_confidence=0.5
    )
    draw_utils   = mp.solutions.drawing_utils
    drawing_spec = draw_utils.DrawingSpec(color=(0,255,0), thickness=1, circle_radius=1)

    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"⚠️ 카메라({camera_index}) 열기 실패")
        mesh.close(); return

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok: break

            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = mesh.process(rgb)

            if res.multi_face_landmarks:
                lm = res.multi_face_landmarks[0].landmark[1]  # nose tip
                nx, ny = int(lm.x * w), int(lm.y * h)

                off_x = int(io.clamp(nx - cx, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))
                off_y = int(io.clamp(cy - ny, -C.MAX_PIXEL_OFF, C.MAX_PIXEL_OFF))  # 화면 위=+

                pan_delta = 0 if abs(off_x) < C.DEAD_ZONE else max(1, int(abs(off_x * C.KP_PAN))) * (1 if off_x > 0 else -1)
                tilt_delta= 0 if abs(off_y) < C.DEAD_ZONE else max(1, int(abs(off_y * C.KP_TILT))) * (1 if off_y > 0 else -1)

                pan_pos  = int(io.clamp(pan_pos  - pan_delta, C.SERVO_MIN, C.SERVO_MAX))
                tilt_pos = int(io.clamp(tilt_pos - tilt_delta, C.SERVO_MIN, C.SERVO_MAX))

                with lock:
                    io.write4(pkt, port, C.PAN_ID,  C.ADDR_GOAL_POSITION, pan_pos)
                    io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, tilt_pos)

                # viz
                cv2.circle(frame, (cx, cy), 5, (255, 0, 0), -1)
                cv2.circle(frame, (nx, ny), 5, (0, 0, 255), -1)
                cv2.putText(frame, f"Off X:{off_x:+d} Y:{off_y:+d}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,255,255), 2)
                if draw_mesh:
                    draw_utils.draw_landmarks(
                        frame, res.multi_face_landmarks[0],
                        mp.solutions.face_mesh.FACEMESH_TESSELATION, drawing_spec, drawing_spec
                    )

            cv2.imshow("Auto-Track Face Center", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                stop_event.set(); break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        mesh.close()
