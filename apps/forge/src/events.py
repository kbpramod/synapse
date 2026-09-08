import asyncio
from typing import Dict

# In-memory store for per-website event queues
_event_queues: Dict[int, asyncio.Queue] = {}


def get_event_queue(website_id: int) -> asyncio.Queue:
    """Return an existing queue for the website or create a new one.
    This function is safe to call multiple times; it will reuse the same queue.
    """
    if website_id not in _event_queues:
        _event_queues[website_id] = asyncio.Queue()
    return _event_queues[website_id]


def publish_event(website_id: int, message: str) -> None:
    """Publish a string message to the event queue for the given website.
    If the queue does not exist yet, it is created.
    """
    queue = get_event_queue(website_id)
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        pass


def clear_event_queue(website_id: int) -> None:
    """Remove the queue for a website after the stream is closed.
    This helps to avoid memory leaks.
    """
    _event_queues.pop(website_id, None)
