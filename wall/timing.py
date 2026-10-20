"""Bounded fixed-step simulation; discard excessive backlog after a stall."""
import math

class FixedStepper:
    def __init__(self, hz=120, max_steps=12):
        if hz <= 0 or max_steps < 1:
            raise ValueError("Invalid physics step configuration")
        self.step = 1.0 / hz
        self.max_steps = max_steps
        self.remainder = 0.0
        self.dropped_time = 0.0

    def advance(self, dt, callback):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError("dt must be finite and non-negative")
        total = self.remainder + dt
        steps = int((total + 1e-12) / self.step)
        count = min(steps, self.max_steps)
        self.dropped_time += max(0, steps - count) * self.step
        self.remainder = max(0.0, total - steps * self.step)
        for _ in range(count):
            callback(self.step)
        return count
