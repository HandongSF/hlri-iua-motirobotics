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
