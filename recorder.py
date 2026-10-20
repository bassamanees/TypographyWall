"""Bounded asynchronous recording with worker-owned writer cleanup."""
import logging
import queue
import threading
import cv2
import numpy as np

class Recorder:
    def __init__(self, path, size, fps):
        w, h = size
        self.size = (w - w % 2, h - h % 2)
        self.path = path
        self.writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, self.size)
        self.ok = self.writer.isOpened()
        self.error = None
        self.dropped = 0
        self._q = queue.Queue(maxsize=12)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        if self.ok:
            self._thread.start()
        else:
            self.writer.release()

    def push(self, raw_rgb, full_size):
        if not self.ok or self._stop.is_set():
            return False
        try:
            self._q.put_nowait((raw_rgb, full_size))
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _run(self):
        try:
            while not self._stop.is_set() or not self._q.empty():
                try:
                    raw, (w, h) = self._q.get(timeout=0.1)
                except queue.Empty:
                    continue
                frame = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
                if (w, h) != self.size:
                    frame = cv2.resize(frame, self.size)
                self.writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        except Exception as exc:
            self.error = str(exc)
            self.ok = False
            logging.getLogger(__name__).exception('Recording failed')
        finally:
            self.writer.release()

    def close(self):
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                self.error = 'Encoder did not finish within five seconds'
                logging.getLogger(__name__).error(self.error)
        return self.error is None
