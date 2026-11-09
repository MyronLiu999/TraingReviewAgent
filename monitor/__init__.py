"""Live monitoring (interfaces for the next phase; not started by the web UI yet).

    feed (MarketFeed) --poll--> Snapshot --> Monitor.step()
                                               |-- triggers.check_plan(): did the active plan's entry /
                                               |   stop / target trade on the new bars?  (code only)
                                               |-- every N new bars: CoachAgent decision (trade plan)
                                               '-- Notifier.send(): alert on triggers and changed calls

Plug in a real data source by subclassing feeds.MarketFeed (see feeds.BrokerFeed for the contract),
and a delivery channel by subclassing notifier.Notifier. Analysis only: nothing here places orders.
"""

from monitor.feeds import CsvFileFeed, MarketFeed, ScreenshotFolderFeed, Snapshot, make_feed
from monitor.notifier import ConsoleNotifier, ListNotifier, Notifier, TelegramNotifier
from monitor.runner import Monitor
from monitor.triggers import check_plan, new_plan_state

__all__ = ["MarketFeed", "Snapshot", "CsvFileFeed", "ScreenshotFolderFeed", "make_feed", "Notifier",
           "ConsoleNotifier", "TelegramNotifier", "ListNotifier", "Monitor", "check_plan", "new_plan_state"]
