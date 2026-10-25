# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
"""In-process pub/sub used to stream live data to websocket clients."""

from __future__ import annotations

import asyncio
import fnmatch
from typing import Any


class Subscription:
    def __init__(self, bus: "Bus", maxsize: int = 2000):
        self.bus = bus
        self.patterns: set[str] = set()
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize)
        self.dropped = 0

    def matches(self, topic: str) -> bool:
        return any(fnmatch.fnmatchcase(topic, p) for p in self.patterns)

    def put(self, topic: str, payload: Any) -> None:
        try:
            self.queue.put_nowait((topic, payload))
        except asyncio.QueueFull:
            self.dropped += 1  # slow client: drop rather than block the engine

    def close(self) -> None:
        self.bus.subs.discard(self)


class Bus:
    def __init__(self):
        self.subs: set[Subscription] = set()

    def subscribe(self) -> Subscription:
        sub = Subscription(self)
        self.subs.add(sub)
        return sub

    def publish(self, topic: str, payload: Any) -> None:
        for sub in list(self.subs):
            if sub.matches(topic):
                sub.put(topic, payload)

    def has_listeners(self, topic: str) -> bool:
        return any(s.matches(topic) for s in self.subs)
