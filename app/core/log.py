import fcntl
import os
import sys
import glob
import time
import datetime
import logging
import signal
from logging.handlers import WatchedFileHandler
from queue import Queue
from logging.handlers import QueueHandler, QueueListener
import threading


LOG_FILE_PATH = "/opt/StreamsAgentGateway_Logs/StreamsAgentGateway.log"

# Use a pidfile path in the same log directory to avoid /run permission issues
pidfile = os.path.join(os.path.dirname(LOG_FILE_PATH), "gunicorn.pid")


# ---------------------------------------------------------------------------
#  WatchedSizeAndTimedRotatingHandler — rotates on size OR midnight
# ---------------------------------------------------------------------------

class WatchedSizeAndTimedRotatingHandler(WatchedFileHandler):
    """
    Rotates on EITHER condition:
        - Size  : when file exceeds `maxBytes`
        - Daily : at midnight
    """

    def __init__(
        self,
        filename: str,
        pidfile: str,
        maxBytes: int = 30 * 1024 * 1024,   # 30 MB
        backupCount: int = 7,
        encoding: str = "utf-8",
        delay: bool = False,
    ):
        self.maxBytes    = maxBytes
        self.backupCount = backupCount
        self.gunicorn_pidfile = pidfile
        self._next_rollover = self._compute_next_midnight()
        self._thread_lock   = threading.Lock()

        try:
            self._current_size = os.path.getsize(filename)
        except OSError:
            self._current_size = 0

        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)

        self._lockfile_path = os.path.abspath(filename) + ".lock"
        self._lockfile_fd = open(self._lockfile_path, "a+")
        self._sync_rollover_from_lockfile()

        super().__init__(filename, encoding=encoding, delay=delay)

    # --- lockfile sync helpers ---

    def _sync_rollover_from_lockfile(self):
        try:
            self._lockfile_fd.seek(0)
            raw = self._lockfile_fd.read().strip()
            if raw:
                stored = float(raw)
                if stored > self._next_rollover:
                    self._next_rollover = stored
        except (ValueError, OSError):
            pass

    def _persist_rollover_to_lockfile(self):
        try:
            self._lockfile_fd.seek(0)
            self._lockfile_fd.truncate()
            self._lockfile_fd.write(str(self._next_rollover))
            self._lockfile_fd.flush()
        except OSError as e:
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid {os.getpid()}] - Failed to persist rollover: {e}\n")

    def _ensure_lockfile_open(self):
        if self._lockfile_fd.closed:
            self._lockfile_fd = open(self._lockfile_path, "a+")
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Reopened closed lockfile fd: {self._lockfile_path}")

    # --- rotation logic ---

    @staticmethod
    def _compute_next_midnight() -> float:
        now = time.localtime()
        return time.mktime((now.tm_year, now.tm_mon, now.tm_mday + 1, 0, 0, 0, 0, 0, -1))

    def _rotate_suffix(self, is_midnight: bool) -> str:
        if is_midnight:
            return (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d_23-59-59")
        return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _do_rotate(self, rotate_reason: str):
        if self.stream:
            self.stream.flush()
            self.stream.close()
            self.stream = None

        is_time_rotation = (rotate_reason == "time")
        suffix = self._rotate_suffix(is_time_rotation)
        rotated_name = f"{self.baseFilename}.{suffix}"
        rotated = False

        if os.path.exists(rotated_name):
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Rotation already done by another worker: {rotated_name}, skipping backup.\n")
        else:
            try:
                os.rename(self.baseFilename, rotated_name)
                sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Rotating log file: {self.baseFilename} → {rotated_name}\n")
                rotated = True
            except FileNotFoundError:
                sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Race on rename — another worker already rotated.\n")

        if rotated:
            self._notify_gunicorn_reopen()

        if is_time_rotation:
            self._next_rollover = self._compute_next_midnight()
            self._persist_rollover_to_lockfile()
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - next rollover will be at : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._next_rollover))}\n")

        self._cleanup_old_backups()
        self._current_size = 0
        self.reopenIfNeeded()
        if self.stream is None:
            self.stream = self._open()

    def _should_rotate(self) -> str | None:
        if time.time() >= self._next_rollover:
            return "time"
        if self._current_size >= self.maxBytes:
            return "size"
        return None

    def _cleanup_old_backups(self):
        try:
            backups = sorted(glob.glob(f"{self.baseFilename}.*"))
            backups = [b for b in backups if not b.endswith(".lock")]
            if len(backups) > self.backupCount:
                for old_log in backups[:len(backups) - self.backupCount]:
                    try:
                        sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Deleting old log backup: {old_log}\n")
                        os.remove(old_log)
                    except OSError:
                        pass
        except Exception as e:
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Log cleanup error: {str(e)}\n")

    def _notify_gunicorn_reopen(self):
        try:
            with open(self.gunicorn_pidfile, "r") as f:
                pid = int(f.read().splitlines()[0])
            os.kill(pid, signal.SIGUSR1)
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Sent SIGUSR1 to Gunicorn master PID {pid}\n")
        except FileNotFoundError:
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Gunicorn pidfile not found: {self.gunicorn_pidfile}\n")
        except ProcessLookupError:
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Gunicorn master PID not running: {self.gunicorn_pidfile}\n")
        except Exception as e:
            sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Failed to send SIGUSR1 to Gunicorn master: {e}\n")

    def emit(self, record: logging.LogRecord):
        try:
            self.reopenIfNeeded()
            if not self._should_rotate():
                super().emit(record)
                try:
                    msg = self.format(record)
                    self._current_size += len(msg.encode("utf-8")) + 1
                except Exception:
                    pass
                return

            with self._thread_lock:
                self._ensure_lockfile_open()
                try:
                    fcntl.flock(self._lockfile_fd, fcntl.LOCK_EX)
                    self._sync_rollover_from_lockfile()
                    rotate_reason = self._should_rotate()
                    if rotate_reason:
                        sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] - [pid:{os.getpid()}] - Rotating log due to '{rotate_reason}'\n")
                        self._do_rotate(rotate_reason)
                finally:
                    fcntl.flock(self._lockfile_fd, fcntl.LOCK_UN)

            super().emit(record)
        except Exception:
            self.handleError(record)

    def close(self):
        try:
            if self._lockfile_fd and not self._lockfile_fd.closed:
                fcntl.flock(self._lockfile_fd, fcntl.LOCK_UN)
                self._lockfile_fd.close()
        except OSError:
            pass
        super().close()


# ---------------------------------------------------------------------------
#  Global async queue-based logging setup
# ---------------------------------------------------------------------------

log_queue = Queue(-1)

file_handler = WatchedSizeAndTimedRotatingHandler(
    filename=LOG_FILE_PATH,
    pidfile=pidfile,
    maxBytes=30 * 1024 * 1024,
    backupCount=7,
)

formatter = logging.Formatter(
    "%(asctime)s - pid:%(process)d - %(levelname)s - [%(name)s:%(lineno)d].%(funcName)s() - %(message)s"
)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

listener = QueueListener(
    log_queue,
    file_handler,
    console_handler,
    respect_handler_level=True,
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(QueueHandler(log_queue))


def start_logging():
    listener.start()


def stop_logging():
    listener.stop()
