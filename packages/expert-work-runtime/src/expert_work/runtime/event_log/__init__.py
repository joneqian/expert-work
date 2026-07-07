"""Event store — append-only event_log access layer.

Algorithm vendored from bytedance/deer-flow runtime/events/store/* @
``813d3c94`` (see module headers); interface re-shaped to ADR-0002.
"""

from expert_work.runtime.event_log.base import EventStore as EventStore
from expert_work.runtime.event_log.db import DbEventStore as DbEventStore
from expert_work.runtime.event_log.memory import InMemoryEventStore as InMemoryEventStore

__all__ = ["DbEventStore", "EventStore", "InMemoryEventStore"]
