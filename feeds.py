"""Fetch and parse RSS / Atom news feeds using only the standard library + requests."""

import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (news-alert-bot)"}
ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class NewsItem:
    uid: str
    source: str
    tier: int
    title: str
    summary: str
    link: str
    published: datetime | None


def _clean(text: str | None, limit: int = 600) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", html.unescape(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)  # RSS (RFC 822)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom (ISO 8601)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _uid(link: str, title: str) -> str:
    return hashlib.sha1(f"{link}|{title}".encode()).hexdigest()


def parse_feed(xml_text: str, source: str, tier: int) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []

    for node in root.iter("item"):  # RSS 2.0
        title = _clean(node.findtext("title"), 300)
        link = (node.findtext("link") or "").strip()
        if not title:
            continue
        items.append(NewsItem(
            uid=_uid(link, title), source=source, tier=tier, title=title,
            summary=_clean(node.findtext("description")), link=link,
            published=_parse_date(node.findtext("pubDate")),
        ))

    for node in root.iter(f"{ATOM}entry"):  # Atom
        title = _clean(node.findtext(f"{ATOM}title"), 300)
        link_el = node.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        if not title:
            continue
        items.append(NewsItem(
            uid=_uid(link, title), source=source, tier=tier, title=title,
            summary=_clean(node.findtext(f"{ATOM}summary") or node.findtext(f"{ATOM}content")),
            link=link,
            published=_parse_date(node.findtext(f"{ATOM}updated") or node.findtext(f"{ATOM}published")),
        ))
    return items


def fetch_all(feeds: list[dict]) -> list[NewsItem]:
    results: list[NewsItem] = []
    for feed in feeds:
        try:
            resp = requests.get(feed["url"], headers=HEADERS, timeout=15)
            resp.raise_for_status()
            results.extend(parse_feed(resp.text, feed["name"], int(feed.get("tier", 3))))
        except (requests.RequestException, ET.ParseError) as e:
            log.warning("Feed failed: %s (%s)", feed["name"], e)
    return results
