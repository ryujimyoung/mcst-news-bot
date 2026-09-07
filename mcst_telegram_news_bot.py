"""Send newly published Ministry of Culture, Sports and Tourism news to Telegram.

No third-party packages are required.  Configure environment variables shown in
.env.example (or set them in the operating system), then run this file.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
MAX_PER_POLL = int(os.environ.get("MAX_PER_POLL", "5"))
# Do not send a story that Google News indexed long after its publisher's time.
# Set to 0 only if you explicitly want every newly discovered result.
MAX_ARTICLE_AGE_MINUTES = int(os.environ.get("MAX_ARTICLE_AGE_MINUTES", "30"))
DB_PATH = os.environ.get("DB_PATH", "mcst_news_seen.sqlite3")
# Korea has no daylight-saving time; a fixed UTC+9 works on Windows and Linux.
KOREA_TIME = timezone(timedelta(hours=9), name="KST")

# Google News Korea RSS requires neither an API key nor a paid account.
NEWS_QUERY = os.environ.get(
    "NEWS_QUERY", '문화체육관광부 OR 문체부 OR "Ministry of Culture, Sports and Tourism"'
)

# Official publisher RSS feeds. Google News remains enabled for every other
# publisher (and for Central Ilbo, whose public general-news RSS is not usable).
DIRECT_RSS_FEEDS = {
    "조선일보": "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",
    "동아일보": "https://rss.donga.com/total.xml",
    "한겨레": "https://www.hani.co.kr/rss/",
    "경향신문": "https://www.khan.co.kr/rss/rssdata/total_news.xml",
    "SBS": "https://news.sbs.co.kr/news/newsflashRssFeed.do?plink=RSSREADER",
    "뉴시스": "https://www.newsis.com/RSS/sokbo.xml",
}
DIRECT_RSS_SOURCES = frozenset(DIRECT_RSS_FEEDS)
RELEVANCE_TERMS = ("문화체육관광부", "문체부", "Ministry of Culture, Sports and Tourism")

# Lightweight, transparent title-based classifier.  It is a signal, not a fact.
POSITIVE = ("성과", "수상", "호평", "확대", "지원", "개선", "증가", "성장", "혁신", "개최", "선정", "활성화", "출범")
NEGATIVE = ("논란", "비판", "의혹", "사과", "중단", "축소", "감사", "수사", "고발", "반발", "적자", "피해", "취소", "징계", "부실")


def init_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.execute("CREATE TABLE IF NOT EXISTS seen (link TEXT PRIMARY KEY, seen_at TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS subscribers (chat_id TEXT PRIMARY KEY, subscribed_at TEXT NOT NULL)"
    )
    connection.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.commit()
    return connection


def was_seen(connection: sqlite3.Connection, link: str) -> bool:
    return connection.execute("SELECT 1 FROM seen WHERE link = ?", (link,)).fetchone() is not None


def remember(connection: sqlite3.Connection, link: str) -> None:
    connection.execute("INSERT OR IGNORE INTO seen(link, seen_at) VALUES (?, ?)", (link, datetime.now(timezone.utc).isoformat()))
    connection.commit()


def subscribe(connection: sqlite3.Connection, chat_id: str) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO subscribers(chat_id, subscribed_at) VALUES (?, ?)",
        (str(chat_id), datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    return cursor.rowcount > 0


def subscribers(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute("SELECT chat_id FROM subscribers")]


def get_setting(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
    connection.commit()


def classify(title: str) -> tuple[str, str]:
    positive_count = sum(word in title for word in POSITIVE)
    negative_count = sum(word in title for word in NEGATIVE)
    if positive_count > negative_count:
        return "긍정", "🟢"
    if negative_count > positive_count:
        return "부정", "🔴"
    return "중립", "⚪"


def is_recent(published: str) -> bool:
    """Return True only for articles published inside the configured time window."""
    if MAX_ARTICLE_AGE_MINUTES <= 0:
        return True
    try:
        published_at = parsedate_to_datetime(published).astimezone(timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - published_at).total_seconds()
        return 0 <= age_seconds <= MAX_ARTICLE_AGE_MINUTES * 60
    except (TypeError, ValueError, IndexError):
        # Without a trustworthy publication time, do not risk sending stale news.
        return False


def is_relevant(title: str, description: str) -> bool:
    searchable = f"{title} {description}".lower()
    return any(term.lower() in searchable for term in RELEVANCE_TERMS)


def get_direct_rss_articles(source: str, url: str) -> list[dict[str, str]]:
    """Read an official publisher feed without stopping other feeds on failure."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "MCST-Telegram-Monitor/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            root = ET.fromstring(response.read())
    except Exception as error:
        print(f"RSS unavailable ({source}): {error}")
        return []

    articles = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        description = (item.findtext("description") or "").strip()
        if title and link and is_recent(published) and is_relevant(title, description):
            articles.append({"title": title, "link": link, "source": source, "published": published})
    return articles


def get_articles() -> list[dict[str, str]]:
    query = urllib.parse.urlencode({"q": NEWS_QUERY, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    request = urllib.request.Request(
        f"https://news.google.com/rss/search?{query}",
        headers={"User-Agent": "Mozilla/5.0 MCST-Telegram-Monitor/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        root = ET.fromstring(response.read())
    articles = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "출처 미상").strip()
        published = (item.findtext("pubDate") or "").strip()
        # Direct RSS is preferred for these sources, avoiding duplicate alerts.
        if title and link and source not in DIRECT_RSS_SOURCES and source != "출처 미상" and is_recent(published):
            articles.append({
                "title": title,
                "link": link,
                "source": source,
                "published": published,
            })
    for source, url in DIRECT_RSS_FEEDS.items():
        articles.extend(get_direct_rss_articles(source, url))
    return articles


def format_message(article: dict[str, str]) -> str:
    label, emoji = classify(article["title"])
    published = article["published"]
    try:
        # GitHub Actions runs in UTC; always show Korean local time to readers.
        published = parsedate_to_datetime(published).astimezone(KOREA_TIME).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, IndexError):
        pass
    return (
        f"{emoji} <b>{label}</b> <i>(제목 기반 자동 분류)</i>\n"
        f"<b>{html.escape(article['title'])}</b>\n"
        f"출처: {html.escape(article['source'])} · {html.escape(published)}\n"
        f'<a href="{html.escape(article["link"], quote=True)}">기사 열기</a>'
    )


def send_telegram(chat_id: str, text: str) -> None:
    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    request = urllib.request.Request(endpoint, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        result = response.read().decode()
    if '"ok":true' not in result:
        raise RuntimeError(f"Telegram API error: {result}")


def sync_subscribers(connection: sqlite3.Connection) -> None:
    """Register people who have pressed Start in a private chat with the bot."""
    parameters: dict[str, str | int] = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    last_update_id = get_setting(connection, "last_update_id")
    if last_update_id:
        parameters["offset"] = int(last_update_id) + 1
    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?{urllib.parse.urlencode(parameters)}"
    with urllib.request.urlopen(endpoint, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {result}")
    for update in result.get("result", []):
        update_id = update.get("update_id")
        if update_id is not None:
            set_setting(connection, "last_update_id", str(update_id))
        message = update.get("message")
        if not message:
            continue
        text = (message.get("text") or "").strip()
        command = text.split(maxsplit=1)[0].lower().split("@", 1)[0] if text else ""
        chat_id = message.get("chat", {}).get("id")
        if command == "/start" and chat_id is not None and subscribe(connection, str(chat_id)):
            send_telegram(str(chat_id), "✅ 구독이 완료됐어요. 새 문체부 관련 보도가 있으면 알려드릴게요.")


def poll_once(connection: sqlite3.Connection, first_run: bool) -> bool:
    sync_subscribers(connection)
    articles = get_articles()
    # On first run, store existing items only. This avoids a flood of old headlines.
    if first_run:
        for article in articles:
            remember(connection, article["link"])
        print(f"Initialized with {len(articles)} existing articles; waiting for new items.")
        return False

    sent = 0
    for article in reversed(articles):  # oldest first
        if was_seen(connection, article["link"]):
            continue
        if sent >= MAX_PER_POLL:
            break
        delivered = False
        for chat_id in subscribers(connection):
            try:
                send_telegram(chat_id, format_message(article))
                delivered = True
            except Exception as error:
                print(f"Delivery failed ({chat_id}): {error}")
        if delivered:
            remember(connection, article["link"])
            sent += 1
            print(f"Sent: {article['title']}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Send new MCST-related news to Telegram.")
    parser.add_argument("--once", action="store_true", help="Check once, send any new items, then exit.")
    arguments = parser.parse_args()
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before running.")
    database_already_exists = Path(DB_PATH).exists()
    connection = init_db()
    subscribe(connection, CHAT_ID)  # Preserve the original recipient as a subscriber.
    first_run = not database_already_exists
    print(f"Monitoring Google News: {NEWS_QUERY} (every {POLL_SECONDS} seconds)")
    if arguments.once:
        poll_once(connection, first_run)
        return
    while True:
        try:
            first_run = poll_once(connection, first_run)
        except Exception as error:  # keep the monitor alive after transient failures
            print(f"[{datetime.now().isoformat(timespec='seconds')}] Error: {error}")
            first_run = False
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
