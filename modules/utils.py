import time

from pyrogram.types import Message


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} PB"


class ProgressTracker:
    """Throttled progress-callback for pyrogram download_media/send_document,
    so we don't hit flood limits editing the status message on every chunk."""

    def __init__(self, status_msg: Message, label: str, min_interval: float = 4.0):
        self.status_msg = status_msg
        self.label = label
        self.min_interval = min_interval
        self._last_edit = 0.0

    async def __call__(self, current: int, total: int):
        now = time.time()
        if now - self._last_edit < self.min_interval and current != total:
            return
        self._last_edit = now
        pct = (current / total * 100) if total else 0
        text = f"{self.label}\n\n{human_size(current)} / {human_size(total)} ({pct:.1f}%)"
        try:
            await self.status_msg.edit(text)
        except Exception:
            pass
