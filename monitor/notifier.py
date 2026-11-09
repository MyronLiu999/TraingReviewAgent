"""Where monitor alerts go. Subclass Notifier for another channel (e-mail, desktop, web push...)."""

import html
import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class Notifier(ABC):
    @abstractmethod
    def send(self, text: str, image_path: str | None = None) -> None:
        """Delivers one alert. Must not raise for delivery problems (log them instead)."""


class ConsoleNotifier(Notifier):
    def send(self, text: str, image_path: str | None = None) -> None:
        print("\n🔔 " + text + (f"\n   图：{image_path}" if image_path else ""), flush=True)


class ListNotifier(Notifier):
    """Collects alerts in memory (tests, or a future web panel)."""
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, text: str, image_path: str | None = None) -> None:
        self.sent.append({"text": text, "image_path": image_path})


class TelegramNotifier(Notifier):
    def __init__(self, tg):
        self.tg = tg

    def send(self, text: str, image_path: str | None = None) -> None:
        try:
            if image_path:
                with open(image_path, "rb") as f:
                    self.tg.send_photo(f.read(), caption=text[:1000])
            else:
                self.tg.send(html.escape(text))
        except Exception:
            log.exception("Telegram alert failed")
