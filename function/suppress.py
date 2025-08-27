# mk2/suppress.py
import os, sys, warnings
from contextlib import contextmanager

# 환경변수/경고 억제
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("GLOG_stderrthreshold", "3")
os.environ.setdefault("GLOG_logtostderr", "0")
os.environ.setdefault("GLOG_alsologtostderr", "0")
if "GLOG_log_dir" not in os.environ:
    import pathlib
    logdir = pathlib.Path.cwd() / "logs"
    logdir.mkdir(exist_ok=True)
    os.environ["GLOG_log_dir"] = str(logdir)

warnings.filterwarnings("ignore")

@contextmanager
def silence_stderr_fd():
    nul = "NUL" if os.name == "nt" else "/dev/null"
    stderr_fd = sys.stderr.fileno()
    keep_fd = os.dup(stderr_fd)
    try:
        null_fd = os.open(nul, os.O_WRONLY)
        os.dup2(null_fd, stderr_fd)
        os.close(null_fd)
        yield
    finally:
        os.dup2(keep_fd, stderr_fd)
        os.close(keep_fd)

def import_cv2_mp():
    with silence_stderr_fd():
        import cv2, mediapipe as mp
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except Exception:
        pass
    return cv2, mp
