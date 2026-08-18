"""Shared slowapi Limiter instance.

A single module-level instance so route decorators (jobs.py) and the app
factory (app.py, which registers it on app.state and the exception
handler) reference the same limiter -- slowapi's decorator pattern reads
app.state.limiter at call time, but the decorator itself is applied at
import time before an app exists.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
