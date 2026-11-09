"""Market data sources for live monitoring.

A feed is polled by the Monitor; `poll()` returns a Snapshot when something new is available (a new
closed bar, a new screenshot) and None otherwise. It must return quickly; a feed that streams can
buffer internally and hand out what it has.

Working now (no account needed):
- CsvFileFeed: a CSV file your charting platform keeps exporting / appending (time,open,high,low,close).
- ScreenshotFolderFeed: a folder where a screenshot tool saves the chart every few minutes.
Placeholder:
- BrokerFeed: where a broker / data-vendor API goes (IBKR, Tradovate, Rithmic, a TradingView webhook,
  Databento, Polygon ...). Implement poll() and register it in FEEDS.
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from market.ohlc import Bar, load_csv

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


@dataclass
class Snapshot:
    symbol: str
    timeframe: str
    as_of: datetime | None = None          # time of the last CLOSED bar included
    csv: str | None = None                 # CSV path or text with the bars up to as_of
    image_path: str | None = None          # current chart screenshot, if any
    new_bars: list[Bar] = field(default_factory=list)   # closed bars since the previous snapshot
    source: str = ""


class MarketFeed(ABC):
    """Contract for every data source."""
    name = "feed"

    def __init__(self, symbol: str = "", timeframe: str = ""):
        self.symbol, self.timeframe = symbol, timeframe

    @abstractmethod
    def poll(self) -> Snapshot | None:
        """A Snapshot if something new is available since the last call, else None. Never blocks long."""

    def close(self) -> None:
        pass


class CsvFileFeed(MarketFeed):
    """Watches a CSV of OHLC bars. `forming_last_bar=True` when the export's last row is the bar that
    is still forming (it is then left out until the next bar starts)."""
    name = "csv"

    def __init__(self, path: str, symbol: str = "", timeframe: str = "", forming_last_bar: bool = False):
        super().__init__(symbol, timeframe)
        self.path, self.forming_last_bar = path, forming_last_bar
        self._mtime = None
        self._last_time: datetime | None = None

    def poll(self) -> Snapshot | None:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            return None
        if mtime == self._mtime:
            return None
        self._mtime = mtime
        bars = load_csv(self.path)
        if self.forming_last_bar:
            bars = bars[:-1]
        if not bars or (self._last_time is not None and bars[-1].time <= self._last_time):
            return None
        new = [b for b in bars if self._last_time is None or b.time > self._last_time]
        first = self._last_time is None
        self._last_time = bars[-1].time
        return Snapshot(self.symbol, self.timeframe, as_of=bars[-1].time, csv=self.path,
                        new_bars=[] if first else new, source=self.name)


class ScreenshotFolderFeed(MarketFeed):
    """Hands out the newest chart image saved into `folder` (older files are ignored)."""
    name = "screenshots"

    def __init__(self, folder: str, symbol: str = "", timeframe: str = ""):
        super().__init__(symbol, timeframe)
        self.folder = folder
        self._seen: float = 0.0

    def poll(self) -> Snapshot | None:
        try:
            files = [os.path.join(self.folder, f) for f in os.listdir(self.folder) if f.lower().endswith(IMAGE_EXT)]
        except OSError:
            return None
        if not files:
            return None
        newest = max(files, key=os.path.getmtime)
        mtime = os.path.getmtime(newest)
        if mtime <= self._seen:
            return None
        self._seen = mtime
        return Snapshot(self.symbol, self.timeframe, as_of=None, image_path=newest, source=self.name)


class BrokerFeed(MarketFeed):
    """Placeholder for a live broker / vendor connection.

    To implement: connect in __init__ (credentials from .env, never from config.toml), subscribe to
    `symbol` bars of `timeframe`, keep closed bars in a buffer, and in poll() return a Snapshot with
    `new_bars` = the bars closed since the last poll and `csv` = all bars so far (CSV text with
    time,open,high,low,close). Read-only market data only: this project never sends orders."""
    name = "broker"

    def __init__(self, symbol: str = "", timeframe: str = "", **settings):
        super().__init__(symbol, timeframe)
        raise NotImplementedError("BrokerFeed is an interface for the live-data phase; use source = \"csv\" "
                                  "or \"screenshots\" in [monitor] for now")

    def poll(self) -> Snapshot | None:      # pragma: no cover - interface only
        raise NotImplementedError


FEEDS = {"csv": CsvFileFeed, "screenshots": ScreenshotFolderFeed, "broker": BrokerFeed}


def make_feed(mcfg: dict) -> MarketFeed:
    """Builds the feed from the [monitor] section of config.toml."""
    source = (mcfg.get("source") or "csv").lower()
    sym, tf = mcfg.get("symbol", ""), mcfg.get("timeframe", "")
    if source == "csv":
        if not mcfg.get("csv_path"):
            raise ValueError("[monitor] source = \"csv\" needs csv_path")
        return CsvFileFeed(mcfg["csv_path"], sym, tf, bool(mcfg.get("forming_last_bar", False)))
    if source == "screenshots":
        if not mcfg.get("screenshot_folder"):
            raise ValueError("[monitor] source = \"screenshots\" needs screenshot_folder")
        return ScreenshotFolderFeed(mcfg["screenshot_folder"], sym, tf)
    if source in FEEDS:
        return FEEDS[source](sym, tf, **(mcfg.get(source) or {}))
    raise ValueError(f"Unknown monitor source {source!r}; choose one of {sorted(FEEDS)}")
