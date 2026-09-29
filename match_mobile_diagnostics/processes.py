"""Child process ownership, including cancellation through GUI → CLI → SSH."""
import os
import signal
import subprocess
import threading

_children = set()
_lock = threading.Lock()


def spawn(argv, **kwargs):
    process = subprocess.Popen(argv, start_new_session=True, **kwargs)
    with _lock:
        _children.add(process)
    return process


def stop(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        except ProcessLookupError:
            pass
    with _lock:
        _children.discard(process)


def stop_all():
    with _lock:
        children = list(_children)
    for process in children:
        stop(process)


def run(argv, *, timeout, input_text=None, env=None):
    process = spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, errors="replace", env=env)
    try:
        out, err = process.communicate(input_text, timeout=timeout)
        return process.returncode, out, err
    finally:
        stop(process)
