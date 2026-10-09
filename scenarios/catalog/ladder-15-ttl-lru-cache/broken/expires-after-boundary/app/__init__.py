import math
from collections import OrderedDict
from fractions import Fraction


def _deadline(start, ttl):
    # Preserve ordinary numeric addition, including normal float rounding.
    if type(start) is float and not math.isfinite(start):
        return start
    try:
        deadline = start + ttl
    except OverflowError:
        return Fraction(start) + Fraction(ttl)
    # Finite float operands can overflow to infinity without raising.
    if type(deadline) is float and math.isinf(deadline):
        return Fraction(start) + Fraction(ttl)
    return deadline


class Cache:
    def __init__(self, capacity, clock):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.capacity, self.clock = capacity, clock
        self.entries = OrderedDict()

    def _purge(self):
        now = self.clock()
        for key, (_, deadline) in list(self.entries.items()):
            if now > deadline:
                del self.entries[key]

    def put(self, key, value, ttl):
        if type(ttl) not in (int, float) or ttl <= 0 or (type(ttl) is float and not math.isfinite(ttl)):
            raise ValueError("ttl must be positive and finite")
        self._purge()
        self.entries.pop(key, None)
        while len(self.entries) >= self.capacity:
            self.entries.popitem(last=False)
        self.entries[key] = (value, _deadline(self.clock(), ttl))

    def get(self, key, default=None):
        self._purge()
        if key not in self.entries:
            return default
        self.entries.move_to_end(key)
        return self.entries[key][0]

    def __len__(self):
        self._purge()
        return len(self.entries)
