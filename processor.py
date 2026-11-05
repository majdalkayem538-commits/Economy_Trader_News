"""Clean the source post, check the AI's draft, render the channel post."""
import hashlib, html, re

URL_RE = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+)", re.I)
MENTION_RE = re.compile(r"(?<!\w)@\w{3,}")
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
SIGNATURE_RE = re.compile(r"^\s*(—|-|–|via|source|المصدر|follow|join|subscribe|📢|🔔).{0,60}$", re.I)
AD_WORDS = ("vip", "join now", "subscribe", "promo", "discount", "free signals", "signal group",
            "اشترك", "انضم", "خصم", "توصيات مجانية", "إعلان", "sponsored", "giveaway")


def clean_source(text):
    """Strip links, @handles, the source's hashtags and signature lines."""
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        if SIGNATURE_RE.match(line) and not re.search(r"\d", line):
            continue
        line = URL_RE.sub("", line)
        line = MENTION_RE.sub("", line)
        line = HASHTAG_RE.sub("", line)
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def looks_like_ad(text):
    t = text.lower()
    return any(w in t for w in AD_WORDS)


def is_worth_sending(text):
    """Cheap filter before spending an AI call."""
    if len(text) < 12 or not re.search(r"[A-Za-z؀-ۿ]{3,}", text):
        return False
    return not looks_like_ad(text)


def fingerprint(text):
    t = re.sub(r"\W+", " ", text.lower()).strip()
    return hashlib.sha1(t.encode()).hexdigest()


AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")


def numbers(text):
    """Numbers as normalized strings: '4.3%' -> '4.3', '1,200' -> '1200', '٤٫٣' -> '4.3'."""
    t = (text or "").translate(AR_DIGITS)
    out = set()
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", t):
        s = m.group(0).replace(",", "")
        s = s.rstrip("0").rstrip(".") if "." in s else s
        out.add(s)
    return out


def invented_numbers(source, draft):
    """Numbers in the draft that never appear in the source — the AI may have made them up."""
    src = numbers(source)
    txt = " ".join(str(draft.get(k, "")) for k in ("headline", "body", "actual", "forecast", "previous",
                                                    "usd_text", "gold_text"))
    txt += " " + " ".join(f"{s.get('condition', '')} {s.get('outcome', '')}" for s in draft.get("scenarios") or [])
    return sorted(n for n in numbers(txt) if n not in src and len(n.replace(".", "")) > 1)


ARROW = {"up": "⬆️", "down": "⬇️", "mixed": "↕️", "neutral": "➖"}
LEVEL = {"high": "🔴", "medium": "🟠", "low": "🟡"}


def validate(d):
    if not isinstance(d, dict):
        raise ValueError("الرد مو object")
    if not d.get("is_news"):
        return d
    for k in ("headline", "body", "usd_text", "gold_text"):
        if not str(d.get(k, "")).strip():
            raise ValueError(f"حقل فاضي: {k}")
    d["usd_direction"] = d.get("usd_direction") if d.get("usd_direction") in ARROW else "neutral"
    d["gold_direction"] = d.get("gold_direction") if d.get("gold_direction") in ARROW else "neutral"
    d["impact_level"] = d.get("impact_level") if d.get("impact_level") in LEVEL else "medium"
    tags = []
    for t in d.get("tags") or []:
        t = "#" + re.sub(r"[^\w]", "_", str(t).lstrip("#").strip()).strip("_")
        if len(t) > 2 and t not in tags:
            tags.append(t)
    d["tags"] = tags[:3]
    sc = []
    for x in d.get("scenarios") or []:
        if isinstance(x, dict) and str(x.get("condition", "")).strip() and str(x.get("outcome", "")).strip():
            sc.append({k: str(x.get(k, "")).strip() for k in ("title", "condition", "outcome")})
    d["scenarios"] = sc[:3]
    return d


def render(d, base_tags, show_scenarios=True):
    """Channel post as Telegram HTML."""
    e = lambda s: html.escape(str(s or "").strip())
    out = [f"{LEVEL[d['impact_level']]} <b>{e(d['headline'])}</b>", "", e(d["body"])]
    figs = [(lbl, d.get(k)) for lbl, k in (("الفعلي", "actual"), ("المتوقع", "forecast"), ("السابق", "previous"))
            if str(d.get(k) or "").strip()]
    if figs:
        out += ["", " | ".join(f"{lbl}: <b>{e(v)}</b>" for lbl, v in figs)]
    out += ["", "📊 <b>الأثر المتوقع:</b>",
            f"💵 الدولار {ARROW[d['usd_direction']]} {e(d['usd_text'])}",
            f"🟡 الذهب {ARROW[d['gold_direction']]} {e(d['gold_text'])}"]
    if show_scenarios and d.get("scenarios"):
        out += ["", "🔮 <b>السيناريوهات المتوقعة:</b>"]
        for i, s in enumerate(d["scenarios"]):
            title = f"<b>{e(s['title'])}:</b> " if s.get("title") else ""
            out.append(f"{'①②③'[i]} {title}{e(s['condition'])} ← {e(s['outcome'])}")
    out += ["", " ".join([base_tags] + d["tags"]).strip()]
    return "\n".join(out)


def admin_preview(post_html, source, draft, draft_id):
    warn = invented_numbers(source, draft)
    head = f"📝 <b>مسودة #{draft_id}</b>"
    rv = draft.get("_review")
    if rv:
        st = rv.get("status")
        if st == "ok":
            head += "\n🔍 المراجع: ✅ مطابقة للخبر"
        elif st == "fixed":
            head += "\n🔍 <b>المراجع صحّح:</b> " + html.escape("؛ ".join(rv.get("issues", [])))
        elif st == "flagged":
            head += "\n🔍 <b>المراجع نبّه:</b> " + html.escape("؛ ".join(rv.get("issues", []))) + " — راجعها"
        else:
            head += "\n🔍 المراجع ما رد — المسودة بدون تدقيق (" + html.escape("".join(rv.get("issues", []))[:80]) + ")"
    if warn:
        head += f"\n⚠️ أرقام مو موجودة بالخبر الأصلي: <code>{html.escape(', '.join(warn))}</code> — راجعها"
    src = html.escape(source[:600])
    return f"{head}\n\n{post_html}\n\n━━━━━━━━━━\n<i>الأصل:</i>\n<code>{src}</code>"
