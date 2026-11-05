"""Where the news comes from.

web     — the channel's public page t.me/s/<channel>. No API ID, no account. Checks every POLL_SECONDS.
userbot — your own Telegram account via Telethon (instant). Needs TG_API_ID, TG_API_HASH, TG_SESSION.
Both call `on_post(post_id, text)` for every new post, oldest first.
"""
import asyncio, html, logging, re
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

log = logging.getLogger("source")


class _PageParser(HTMLParser):
    """Pulls (post_id, text, iso_date) out of t.me/s/<channel>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.posts, self.cur = [], None
        self.depth = 0            # div depth inside the message text
        self.in_text = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "div" and "js-widget_message" in cls.split() and a.get("data-post"):
            pid = a["data-post"].rsplit("/", 1)[-1]
            if pid.isdigit():
                self.cur = {"id": int(pid), "parts": [], "date": None}
                self.posts.append(self.cur)
            return
        if self.cur is None:
            return
        if tag == "div" and self.in_text:
            self.depth += 1
        elif tag == "div" and "js-message_text" in cls.split() and not self.cur["parts"]:
            self.in_text, self.depth = True, 0
        elif tag == "br" and self.in_text:
            self.cur["parts"].append("\n")
        elif tag == "time" and a.get("datetime") and self.cur["date"] is None:
            self.cur["date"] = a["datetime"]

    def handle_endtag(self, tag):
        if tag == "div" and self.in_text:
            if self.depth == 0:
                self.in_text = False
            else:
                self.depth -= 1

    def handle_data(self, data):
        if self.in_text and self.cur is not None:
            self.cur["parts"].append(data)


def parse_channel_page(page):
    p = _PageParser()
    p.feed(page)
    out = {}
    for x in p.posts:
        text = re.sub(r"\n{3,}", "\n\n", "".join(x["parts"])).strip()
        out[x["id"]] = {"id": x["id"], "text": html.unescape(text), "date": x["date"]}
    return [out[k] for k in sorted(out)]


class WebSource:
    URL = "https://t.me/s/{ch}"

    def __init__(self, channel, store, poll_seconds=20, catchup_minutes=120, client=None):
        self.ch = channel.lstrip("@")
        self.store, self.poll, self.catchup = store, poll_seconds, catchup_minutes
        self.http = client or httpx.AsyncClient(timeout=30, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"})

    async def fetch(self):
        r = await self.http.get(self.URL.format(ch=self.ch))
        r.raise_for_status()
        posts = parse_channel_page(r.text)
        if not posts and "tgme_channel_info" not in r.text:
            raise RuntimeError("الصفحة ما فيها منشورات — القناة خاصة أو المعاينة مسكّرة")
        return posts

    async def check(self, on_post):
        posts = await self.fetch()
        if not posts:
            return 0
        last = self.store.get("last_src_id")
        if last is None:                       # first run: start from now
            self.store.set("last_src_id", posts[-1]["id"])
            log.info("web source ready, latest post %s", posts[-1]["id"])
            return 0
        new = [p for p in posts if p["id"] > int(last)]
        now = datetime.now(timezone.utc).timestamp()
        for p in new:
            self.store.set("last_src_id", p["id"])
            try:
                ts = datetime.fromisoformat(p["date"]).timestamp() if p["date"] else now
            except ValueError:
                ts = now
            if p["text"] and now - ts <= self.catchup * 60:
                await on_post(p["id"], p["text"])
        return len(new)

    async def run(self, on_post, on_error):
        fails = 0
        while True:
            try:
                await self.check(on_post)
                fails = 0
            except Exception as e:
                fails += 1
                log.warning("web source: %s", e)
                if fails in (5, 50):
                    await on_error(f"ما عم اقدر اقرأ t.me/s/{self.ch} ({e.__class__.__name__}: {e})")
            await asyncio.sleep(self.poll if fails < 5 else min(300, self.poll * fails))


class UserbotSource:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store

    async def run(self, on_post, on_error):
        from telethon import TelegramClient, events, utils
        from telethon.sessions import StringSession
        c = self.cfg
        client = TelegramClient(StringSession(c.SESSION), c.API_ID, c.API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await on_error("TG_SESSION مو صالح — اعمل واحد جديد بـ login.py")
            return
        ent = await client.get_entity(c.SOURCE)
        peer = utils.get_peer_id(ent)

        @client.on(events.NewMessage(chats=peer))
        async def _(ev):
            self.store.set("last_src_id", ev.id)
            if ev.message.message:
                await on_post(ev.id, ev.message.message)

        last = self.store.get("last_src_id")
        if last:
            cutoff = datetime.now(timezone.utc).timestamp() - c.CATCHUP_MINUTES * 60
            for m in reversed(await client.get_messages(ent, min_id=int(last), limit=30)):
                self.store.set("last_src_id", max(int(self.store.get("last_src_id")), m.id))
                if m.message and m.date.timestamp() >= cutoff:
                    await on_post(m.id, m.message)
        log.info("userbot listening to %s", c.SOURCE)
        await client.run_until_disconnected()
