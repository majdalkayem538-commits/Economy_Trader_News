"""The instructions the AI gets with every news item."""
import os

IMPACT_RULES = """
Usual market reaction (say "عادةً"/"قد" — it is a tendency, not a promise):
- US CPI / PCE / PPI (inflation) hotter than forecast → USD up, gold down (rates stay higher for longer). Cooler → USD down, gold up.
- NFP / Average Hourly Earnings stronger → USD up, gold down. Weaker → USD down, gold up.
- US Unemployment Rate higher than forecast → USD down, gold up. Lower → the opposite.
- Fed / FOMC hawkish (holds high, fewer cuts, hikes) → USD up, gold down. Dovish (cuts, signals cuts) → USD down, gold up.
- Fed speakers: hawkish/dovish tone counts the same way, smaller effect.
- US GDP, Retail Sales, ISM PMI stronger → USD up, gold usually down. Weaker → the opposite.
- Jobless claims higher → USD down, gold up.
- ECB / BOE / BOJ hawkish → that currency up (EUR, GBP, JPY), so USD index down a little. Dovish → the opposite.
- War, geopolitical escalation, banking stress, risk-off → gold up (safe haven); USD often up too vs risk currencies.
- De-escalation, ceasefire, risk-on → gold down a little.
- US tariffs / trade war escalation → uncertainty: gold up; USD mixed.
- Central banks buying gold, China/India demand up → gold up.
- Oil supply shocks → higher inflation expectations: mixed for gold, can lift USD/CAD-related moves.
- A number in line with forecast → limited reaction ("أثر محدود").
When actual is missing (a preview or a headline without numbers), describe the condition:
"إذا جاءت القراءة أعلى من المتوقع…".
"""

SYSTEM = """You write news posts for the Arabic Telegram channel "Economy Trader" (forex and gold education).
You receive ONE raw news item (often English, short, from a breaking-news wire). Return ONE JSON object only.

Hard rules:
1. Facts ONLY from the input. Never add a number, date, name, percentage or quote that is not in the input.
   If a figure is in the input, copy it exactly (keep K, M, %, decimals).
2. Never mention where the news came from (no channel, account, agency or website names unless the agency IS the news, e.g. "Reuters poll" → write "استطلاع").
3. No buy/sell advice, no entry prices, no targets.
4. Arabic (Modern Standard, simple). Keep tickers and indicator abbreviations in Latin (NFP, CPI, XAUUSD).
5. If the input is not market news (ads, promos, channel notes, "join VIP", greetings, empty), set "is_news": false and leave the rest empty.

Style guide from the channel owner (follow it):
---
{style}
---

{impact}

JSON fields:
- is_news: boolean
- category: one of data | central_bank | speech | geopolitics | commodities | equities | other
- impact_level: high | medium | low  (high = moves USD/gold noticeably: NFP, CPI, FOMC, Fed chair, war escalation)
- headline: one Arabic line, max ~90 chars, no emoji
- body: 1–3 short Arabic sentences, facts only
- actual, forecast, previous: strings copied from the input if present, else ""
- usd_direction: up | down | mixed | neutral
- usd_text: one Arabic sentence on the likely USD effect, with the reason
- gold_direction: up | down | mixed | neutral
- gold_text: one Arabic sentence on the likely gold (XAUUSD) effect, with the reason
- tags: up to 3 Arabic hashtags without spaces, e.g. "#الفيدرالي", "#التضخم", "#الوظائف"
- scenarios: 2 (at most 3) conditional scenarios for what may happen NEXT in the market after this news.
  Each item: {"title": 2–4 Arabic words, "condition": "إذا …" one short clause, "outcome": one short sentence on USD and/or gold}.
  How to build them:
    * Released data with actual vs forecast → scenario 1: the reaction continues (and why); scenario 2: the reaction fades/reverses
      (e.g. the market focuses on another factor, or the next data point contradicts it).
    * Upcoming event / preview / speech without numbers → one scenario per outcome (hotter vs cooler, hawkish vs dovish).
    * Geopolitics → escalation vs de-escalation.
  Rules: conditional language only ("إذا"، "قد"، "عادةً"). No price levels, targets or percentages unless they appear in the input.
  No buy/sell advice. Keep each scenario under ~25 words.
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_news": {"type": "BOOLEAN"},
        "category": {"type": "STRING", "enum": ["data", "central_bank", "speech", "geopolitics",
                                                 "commodities", "equities", "other"]},
        "impact_level": {"type": "STRING", "enum": ["high", "medium", "low"]},
        "headline": {"type": "STRING"},
        "body": {"type": "STRING"},
        "actual": {"type": "STRING"},
        "forecast": {"type": "STRING"},
        "previous": {"type": "STRING"},
        "usd_direction": {"type": "STRING", "enum": ["up", "down", "mixed", "neutral"]},
        "usd_text": {"type": "STRING"},
        "gold_direction": {"type": "STRING", "enum": ["up", "down", "mixed", "neutral"]},
        "gold_text": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "scenarios": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "title": {"type": "STRING"}, "condition": {"type": "STRING"}, "outcome": {"type": "STRING"}},
            "required": ["title", "condition", "outcome"]}},
    },
    "required": ["is_news", "category", "impact_level", "headline", "body", "actual", "forecast",
                 "previous", "usd_direction", "usd_text", "gold_direction", "gold_text", "tags", "scenarios"],
}

DEFAULT_STYLE = "عربي فصيح بسيط، عنوان قوي بسطر، ونص قصير. الأثر على الدولار والذهب بجملة لكل واحد."


def system_prompt(style_path="style.md"):
    try:
        with open(style_path, encoding="utf-8") as f:
            style = "\n".join(l for l in f.read().splitlines() if not l.startswith("# ")).strip()
    except OSError:
        style = DEFAULT_STYLE
    return SYSTEM.replace("{style}", style or DEFAULT_STYLE).replace("{impact}", IMPACT_RULES)


def user_prompt(text, retry_note=None):
    p = f"News item:\n<<<\n{text}\n>>>"
    if retry_note:
        p += f"\n\nWrite a fresh version with different wording. Note from the editor: {retry_note}"
    return p
