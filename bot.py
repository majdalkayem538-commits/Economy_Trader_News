#!/usr/bin/env python3
"""
Economy Trader — بوت الأخبار.

1. بيقرأ كل منشور جديد بقناة المصدر:
   - SOURCE_MODE=web (الافتراضي): من صفحة القناة العامة t.me/s/… — بدون API ID وبدون حسابك.
   - SOURCE_MODE=userbot: من حسابك (فوري) — بدو TG_API_ID و TG_API_HASH و TG_SESSION.
2. بينظف الخبر ويبعته للذكاء الاصطناعي ليصيغه بأسلوب Economy Trader مع أثره على الدولار والذهب.
3. بتوصلك مسودة لكل خبر مع أزرار: نشر / تعديل / صياغة جديدة / تجاهل.
4. لما تضغط نشر، البوت بينشر نص بس بقناتك. ما في ذكر للمصدر.

Run: python bot.py
"""
import asyncio, logging, os

from config import Config as C
from llm import AIError, Writer
from review import Reviewer
from processor import admin_preview, clean_source, fingerprint, is_worth_sending, render, validate
from sources import UserbotSource, WebSource
from store import Store
from tgapi import BotAPI, TelegramError, button, entities_to_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

api: BotAPI = None
store: Store = None
writer: Writer = None
reviewer: Reviewer = None
queue: asyncio.Queue = None
pending_edit = {}          # admin_id -> draft_id


def kb(did):
    return [[button("✅ نشر", f"pub:{did}"), button("✏️ تعديل", f"edit:{did}")],
            [button("🔄 صياغة جديدة", f"redo:{did}"), button("🗑 تجاهل", f"skip:{did}")]]


def paused():
    return store.get("paused") == "1"


async def tell_admins(text, buttons=None):
    sent = {}
    for a in C.ADMINS:
        try:
            m = await api.send(a, text, buttons)
            sent[a] = m["message_id"]
        except TelegramError as e:
            log.error("can't reach admin %s (pressed Start?): %s", a, e)
    return sent


async def show_to_admins(did, edit=False):
    d = store.draft(did)
    text = admin_preview(d["post"], d["src_text"], d["data"], did)
    msgs = dict(d["admin_msgs"])
    for a in C.ADMINS:
        try:
            if edit and a in msgs:
                await api.edit(a, msgs[a], text, kb(did))
            else:
                m = await api.send(a, text, kb(did))
                msgs[a] = m["message_id"]
        except TelegramError as e:
            log.error("admin %s: %s", a, e)
    store.update(did, admin_msgs=msgs)


async def mark_admins(did, note):
    d = store.draft(did)
    for a, mid in d["admin_msgs"].items():
        try:
            await api.edit(a, mid, f"{note}\n\n{d['post'] or ''}")
        except TelegramError as e:
            log.warning("mark admin %s: %s", a, e)


async def write_draft(did, temperature=0.4, note=None):
    d = store.draft(did)
    data = validate(await writer.rewrite(d["src_text"], temperature, note))
    if not data.get("is_news") or (C.SKIP_LOW and data["impact_level"] == "low"):
        store.update(did, status="skipped", data=data)
        return False
    if reviewer and (C.REVIEW_MODE == "all" or (C.REVIEW_MODE == "high" and data["impact_level"] == "high")):
        data, info = await reviewer.review(d["src_text"], data)
        data["_review"] = info
        log.info("review #%s: %s %s", did, info["status"], info["issues"])
    store.update(did, data=data, post=render(data, C.HASHTAGS, C.SCENARIOS), status="pending")
    return True


# ------------------------------------------------------------------ news pipeline
async def on_post(post_id, raw):
    if paused():
        return
    await queue.put((post_id, raw))


async def worker():
    while True:
        post_id, raw = await queue.get()
        try:
            await handle_news(post_id, raw)
        except Exception as e:
            log.exception("worker: %s", e)
        finally:
            queue.task_done()


async def handle_news(post_id, raw, forced=False):
    text = clean_source(raw)
    if not forced and not is_worth_sending(text):
        log.info("skip (not news/ad): %s", text[:60]); return
    fp = fingerprint(text)
    if not forced and store.seen(fp):
        log.info("skip duplicate"); return
    did = store.add(post_id, text, fp)
    try:
        ok = await write_draft(did)
    except (AIError, ValueError) as e:
        store.update(did, status="failed")
        await tell_admins(f"⚠️ <b>فشلت الصياغة</b> (مسودة #{did}): <code>{e}</code>\n\n<code>{text[:800]}</code>",
                          [[button("🔄 جرّب مرة تانية", f"redo:{did}"), button("🗑 تجاهل", f"skip:{did}")]])
        return
    if ok:
        await show_to_admins(did)
        log.info("draft #%s sent", did)
    else:
        log.info("draft #%s skipped by AI (not news / low impact)", did)


# ------------------------------------------------------------------ admin side
async def on_button(q):
    uid = q["from"]["id"]
    if uid not in C.ADMINS:
        return await api.answer(q["id"], "مو مسموح", True)
    action, _, did = (q.get("data") or "").partition(":")
    if not did.isdigit():
        return await api.answer(q["id"])
    did = int(did)
    d = store.draft(did)
    if not d:
        return await api.answer(q["id"], "المسودة مو موجودة", True)
    if d["status"] == "published":
        return await api.answer(q["id"], "هالمسودة انتشرت من قبل")
    if d["status"] == "ignored" and action != "redo":
        return await api.answer(q["id"], "متجاهَلة — اضغط صياغة جديدة لترجعها")

    if action == "pub":
        if not d["post"]:
            return await api.answer(q["id"], "ما في نص للنشر", True)
        try:
            m = await api.send(C.TARGET, d["post"])
        except TelegramError as e:
            return await api.answer(q["id"], f"ما قدرت انشر — تأكد إنو البوت أدمن بالقناة\n{e}"[:190], True)
        store.update(did, status="published", channel_msg_id=m["message_id"])
        link = f"https://t.me/{C.TARGET.lstrip('@')}/{m['message_id']}" if C.TARGET.startswith("@") else ""
        await api.answer(q["id"], "✅ انتشر")
        await mark_admins(did, f"✅ <b>انتشر</b> {link}".strip())
    elif action == "skip":
        store.update(did, status="ignored")
        await api.answer(q["id"], "🗑 تجاهلت")
        await mark_admins(did, "🗑 <b>متجاهَلة</b>")
    elif action == "edit":
        pending_edit[uid] = did
        await api.answer(q["id"])
        await api.send(uid, f"✏️ ابعت النص الجديد لمسودة #{did} كامل (بتقدر تستعمل bold).\n/cancel للإلغاء")
    elif action == "redo":
        await api.answer(q["id"], "🔄 عم اكتبها من جديد…")
        try:
            ok = await write_draft(did, temperature=0.8, note="اكتبها بصياغة مختلفة")
        except (AIError, ValueError) as e:
            return await api.send(uid, f"⚠️ فشلت: <code>{e}</code>")
        if ok:
            await show_to_admins(did, edit=bool(store.draft(did)["admin_msgs"]))
        else:
            await api.send(uid, f"الذكاء الاصطناعي اعتبر #{did} مو خبر. إذا بدك ياه، استعمل /test مع النص.")


HELP = ("أهلين 👋 رح توصلك المسودات هون.\n/status — الحالة\n/pause — إيقاف مؤقت\n/resume — استئناف\n"
        "/test نص الخبر — جرّب صياغة خبر يدوياً\n/cancel — إلغاء التعديل")


async def on_message(m):
    if m.get("chat", {}).get("type") != "private":
        return
    uid = m["from"]["id"]
    t = (m.get("text") or "").strip()
    if uid not in C.ADMINS:
        if t.startswith("/start"):
            await api.send(uid, f"هاد بوت خاص.\nرقمك: <code>{uid}</code>")
        return
    if t.startswith("/start"):
        return await api.send(uid, HELP + f"\n\nرقمك: <code>{uid}</code>")
    if t.startswith("/cancel"):
        pending_edit.pop(uid, None)
        return await api.send(uid, "تمام، ألغيت.")
    if t.startswith("/status"):
        s = store.stats()
        return await api.send(uid, f"{'⏸ موقف مؤقتاً' if paused() else '▶️ شغّال'} | المصدر: {C.SOURCE_MODE}\n"
                                   f"بالانتظار: {s['pending']}\nانتشر آخر 24 ساعة: {s['published_24h']}\n"
                                   f"تجاهلت: {s['ignored_24h']} | مو خبر: {s['skipped_24h']} | فشل: {s['failed_24h']}\n"
                                   f"الطابور: {queue.qsize()} | آخر منشور مقروء: {store.get('last_src_id')}\n"
                                   f"الموديل: {C.PROVIDER} | المراجع: "
                                   f"{(reviewer.provider + ':' + reviewer.model + ' (' + C.REVIEW_MODE + ')') if reviewer else 'مطفي'}")
    if t.startswith("/pause"):
        store.set("paused", "1")
        return await api.send(uid, "⏸ وقفت. الأخبار الجديدة ما رح تنصاغ لحد /resume")
    if t.startswith("/resume"):
        store.set("paused", "0")
        return await api.send(uid, "▶️ رجعت اشتغل")
    if t.startswith("/test"):
        txt = t[5:].strip()
        if not txt:
            return await api.send(uid, "اكتب: /test وبعدها نص الخبر")
        await api.send(uid, "⏳ عم صيغه…")
        return await handle_news(0, txt, forced=True)
    if uid in pending_edit and t:
        did = pending_edit.pop(uid)
        store.update(did, post=entities_to_html(m.get("text"), m.get("entities")), status="pending")
        await show_to_admins(did, edit=True)
        return await api.send(uid, f"✔ عدّلت مسودة #{did} — راجعها واضغط نشر.")


async def poll_updates():
    offset = int(store.get("tg_offset", "0") or 0)
    while True:
        try:
            ups = await api.updates(offset)
        except TelegramError as e:
            log.error("getUpdates: %s", e)
            if "Conflict" in str(e):
                log.error("في نسخة تانية من البوت شغّالة بنفس التوكن — سكّرها")
            await asyncio.sleep(10); continue
        for u in ups or []:
            offset = u["update_id"] + 1
            store.set("tg_offset", offset)
            try:
                if "callback_query" in u:
                    await on_button(u["callback_query"])
                elif "message" in u:
                    await on_message(u["message"])
            except Exception as e:
                log.exception("update: %s", e)


# ------------------------------------------------------------------ main
def make_reviewer():
    if C.REVIEW_MODE == "off":
        return None
    prov = C.REVIEW_PROVIDER
    if prov == "deepseek" and not C.DEEPSEEK_KEY:
        log.warning("REVIEW_PROVIDER=deepseek بس DEEPSEEK_API_KEY فاضي — المراجع رح يكون Gemini")
        prov = "gemini"
    if prov == "deepseek":
        return Reviewer("deepseek", C.DEEPSEEK_KEY, C.REVIEW_MODEL or "deepseek-chat")
    return Reviewer("gemini", C.GEMINI_KEY, C.REVIEW_MODEL or "gemini-3.5-flash-lite")



async def main():
    global api, store, writer, queue, reviewer
    C.validate()
    reviewer = make_reviewer()
    os.makedirs(C.DATA_DIR, exist_ok=True)
    api, store, writer, queue = BotAPI(C.BOT_TOKEN), Store(C.DB_PATH), Writer(C), asyncio.Queue()
    me = await api.me()
    await api.call("deleteWebhook")
    src = UserbotSource(C, store) if C.SOURCE_MODE == "userbot" else \
        WebSource(C.SOURCE, store, C.POLL_SECONDS, C.CATCHUP_MINUTES)
    log.info("@%s: source=%s (%s), publish to %s, admins %s",
             me["username"], C.SOURCE, C.SOURCE_MODE, C.TARGET, C.ADMINS)

    async def on_error(msg):
        await tell_admins(f"⚠️ {msg}")

    await tell_admins(f"▶️ بوت الأخبار اشتغل — المصدر: {C.SOURCE} ({C.SOURCE_MODE})")
    await asyncio.gather(worker(), poll_updates(), src.run(on_post, on_error))


if __name__ == "__main__":
    asyncio.run(main())
