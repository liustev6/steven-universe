"""Admission (speech-service.md T13): per kind, `running` sessions at once (the batch's slots) and
`waiting` more in line; anyone past that is refused at once (`503`, `Retry-After`), not queued for
long: a voice reply that starts seconds late is worse than a retry.

    ticket = admission.enter()      Refused if full; else running at once, or waiting (its position)
    await ticket.started(timeout)   a waiting ticket: until a running one leaves (QueueTimeout after)
    ticket.release()                leaving, however it ends: the first waiting ticket starts

Single-threaded: used from the event loop only (the sessions), never from the worker threads.
"""

from __future__ import annotations

import asyncio
from collections import deque


class Refused(Exception):
    """Every running place and every waiting place is taken."""


class QueueTimeout(Exception):
    """Waited too long for a running place."""


class Ticket:
    def __init__(self, admission: Admission, *, running: bool):
        self._admission, self.running, self.released = admission, running, False
        self._event = asyncio.Event()
        if running:
            self._event.set()

    @property
    def position(self) -> int:
        """0 when running; else the place in line (1: next)."""
        return 0 if self.running else self._admission._queue.index(self) + 1

    async def started(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            self.release()
            raise QueueTimeout(f"no place within {timeout:g} s") from None

    def release(self) -> None:
        if not self.released:
            self.released = True
            self._admission._leave(self)

    def _start(self) -> None:
        self.running = True
        self._event.set()


class Admission:
    def __init__(self, running: int, waiting: int):
        self.running_max, self.waiting_max = running, waiting
        self._running = 0
        self._queue: deque[Ticket] = deque()

    def enter(self) -> Ticket:
        if self._running < self.running_max and not self._queue:
            self._running += 1
            return Ticket(self, running=True)
        if len(self._queue) < self.waiting_max:
            ticket = Ticket(self, running=False)
            self._queue.append(ticket)
            return ticket
        raise Refused(f"{self.running_max} running and {self.waiting_max} waiting already")

    def counts(self) -> dict[str, int]:
        return {"running": self._running, "waiting": len(self._queue),
                "running_max": self.running_max, "waiting_max": self.waiting_max}

    def _leave(self, ticket: Ticket) -> None:
        if ticket.running:
            self._running -= 1
        elif ticket in self._queue:
            self._queue.remove(ticket)
        while self._running < self.running_max and self._queue:
            self._running += 1
            self._queue.popleft()._start()
