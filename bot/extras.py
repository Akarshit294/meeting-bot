"""Optional in-call features that must never touch the call: each person's camera picture (task 11) and
the speaker timeline (task 12). Each returns an object with tick() and stop(), or None."""
from __future__ import annotations


def start(call):
    return None, None
