#!/usr/bin/env python3
"""
اختياري — بس إذا بدك SOURCE_MODE=userbot (قراءة فورية من حسابك بدل صفحة الويب).
شغّل هالملف على جهازك (مرة وحدة) ليطلعلك TG_SESSION.

  pip install telethon
  python login.py

رح يطلب API ID و API HASH ورقمك والكود اللي بيوصلك على تلغرام.
النتيجة سطر طويل = مفتاح دخول كامل لحسابك. حطه بـ Render كـ TG_SESSION وما تبعته لحدا.
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id = int(input("TG_API_ID: ").strip())
    api_hash = input("TG_API_HASH: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as c:
        me = await c.get_me()
        s = c.session.save()
        print("\n✔ تم الدخول:", me.first_name, "| ADMIN_IDS =", me.id)
        print("\nTG_SESSION (انسخ السطر كامل):\n")
        print(s)
        with open("tg_session.txt", "w") as f:
            f.write(s)
        print("\n(وانحفظ كمان بـ tg_session.txt — امسحه بعد ما تحطه بـ Render)")


asyncio.run(main())
