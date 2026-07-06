"""
pwsh_reader.py
Background reader thread. pywinpty read() BLOCKS when the shell is idle, so it must
run off the main loop; the main loop scans a buffer the reader fills. This mirrors
the spike finding (a blocking read on the main thread hangs the whole session).
"""

import time
import threading
import logging

logger = logging.getLogger(__name__)


class ReaderThread:
    """Continuously drains the pty into an on_data callback until EOF/stop."""

    def __init__(self, proc, on_data, chunk=4096):
        self.proc = proc
        self.on_data = on_data
        self.chunk = chunk
        self._alive = True
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while self._alive:
            try:
                data = self.proc.read(self.chunk)
            except EOFError:
                logger.debug("reader: EOF")
                break
            except Exception as exc:  # pty closed / process gone
                logger.debug("reader stopped: %s", exc)
                break
            if data:
                try:
                    self.on_data(data)
                except Exception:
                    logger.exception("on_data callback failed")
            else:
                time.sleep(0.01)

    def stop(self):
        self._alive = False
