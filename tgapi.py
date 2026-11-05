"""Minimal Telegram Bot API client (HTTPS) — needs only BOT_TOKEN, no API ID."""
import asyncio, html, logging
import httpx

log = logging.getLogger("tgapi")


class BotAPI:
    def __init__(self, token, client=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.http = client or httpx.AsyncClient(timeout=70)

    async def call(self, method, **params):
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(5):
            try:
                r = await self.http.post(self.base + method, json=params)
                d = r.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("%s: %s", method, e.__class__.__name__)
                await asyncio.sleep(3 * (attempt + 1)); continue
            if d.get("ok"):
                return d["result"]
            retry = (d.get("parameters") or {}).get("retry_after")
            if retry:
                await asyncio.sleep(retry + 1); continue
            desc = d.get("description", "")
            if "message is not modified" in desc:
                return None
            raise TelegramError(f"{method}: {desc}")
        raise TelegramError(f"{method}: no response")

    async def send(self, chat_id, text, buttons=None):
        return await self.call("sendMessage", chat_id=chat_id, text=text[:4096], parse_mode="HTML",
                               link_preview_options={"is_disabled": True},
                               reply_markup={"inline_keyboard": buttons} if buttons else None)

    async def edit(self, chat_id, message_id, text, buttons=None):
        return await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4096],
                               parse_mode="HTML", link_preview_options={"is_disabled": True},
                               reply_markup={"inline_keyboard": buttons or []})

    async def answer(self, callback_id, text=None, alert=False):
        try:
            await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=alert)
        except TelegramError:
            pass

    async def updates(self, offset, timeout=50):
        return await self.call("getUpdates", offset=offset, timeout=timeout,
                               allowed_updates=["message", "callback_query"])

    async def me(self):
        return await self.call("getMe")


class TelegramError(Exception):
    pass


def button(text, data):
    return {"text": text, "callback_data": data}


def entities_to_html(text, entities):
    """Bot API text + entities (UTF-16 offsets) -> Telegram HTML, keeping the admin's formatting."""
    if not text:
        return ""
    u = text.encode("utf-16-le")
    n = len(u) // 2
    opens, closes = {}, {}
    tags = {"bold": ("<b>", "</b>"), "italic": ("<i>", "</i>"), "underline": ("<u>", "</u>"),
            "strikethrough": ("<s>", "</s>"), "code": ("<code>", "</code>"), "pre": ("<pre>", "</pre>"),
            "spoiler": ("<tg-spoiler>", "</tg-spoiler>"), "blockquote": ("<blockquote>", "</blockquote>")}
    for e in entities or []:
        t = e.get("type")
        if t == "text_link":
            o, c = f'<a href="{html.escape(e["url"], quote=True)}">', "</a>"
        elif t in tags:
            o, c = tags[t]
        else:
            continue
        s, end = e["offset"], e["offset"] + e["length"]
        opens.setdefault(s, []).append((end, o))
        closes.setdefault(end, []).insert(0, c)
    out = []
    for i in range(n + 1):
        out += closes.get(i, [])
        out += [o for _, o in sorted(opens.get(i, []), key=lambda x: -x[0])]
        if i < n:
            ch = u[2 * i: 2 * i + 2]
            if 0xD800 <= int.from_bytes(ch, "little") <= 0xDBFF:   # high surrogate: emit pair at its end
                continue
            prev = u[2 * i - 2: 2 * i] if i else b""
            if prev and 0xD800 <= int.from_bytes(prev, "little") <= 0xDBFF:
                out.append(html.escape((prev + ch).decode("utf-16-le")))
            else:
                out.append(html.escape(ch.decode("utf-16-le")))
    return "".join(out)
