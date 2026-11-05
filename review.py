"""Second AI that fact-checks every draft against the source before you see it.

Writer (Gemini) → Reviewer (DeepSeek, or a second Gemini model) → your preview.
The reviewer fixes what it finds and lists the issues above the draft.
"""
import json, logging

import httpx

from llm import AIError, Throttle, parse_json
from prompt import IMPACT_RULES
from processor import validate

log = logging.getLogger("review")

REVIEW_SYSTEM = """You are a strict fact-checker for an Arabic forex/gold news channel ("Economy Trader").
You get the SOURCE news item and a DRAFT post (JSON) written by another AI. Check the draft against the source:

1. Every number, percentage, date, name and quote in the draft must exist in the source. Remove or fix anything invented.
2. The meaning must match: correct actual vs forecast comparison ("أعلى/أقل من المتوقع"), no exaggeration,
   no facts added from outside the source.
3. usd_direction / gold_direction and their texts must follow the usual market reactions below and the source facts.
4. scenarios: conditional only ("إذا … قد …"), no price levels or targets unless in the source, no buy/sell advice.
5. No mention of any news channel, account or source.
6. Arabic must be clear and correct.

{impact}

Return ONE JSON object:
{"ok": true|false,
 "issues": ["short Arabic description of each problem you found"],
 "fixed": <the full corrected draft with exactly the same fields as DRAFT> or null}
If the draft is correct: {"ok": true, "issues": [], "fixed": null}. Do not rewrite a correct draft just for style.
""".replace("{impact}", IMPACT_RULES)


def review_user(source, draft):
    clean = {k: v for k, v in draft.items() if not k.startswith("_")}
    return f"SOURCE:\n<<<\n{source}\n>>>\n\nDRAFT:\n{json.dumps(clean, ensure_ascii=False, indent=1)}"


class Reviewer:
    def __init__(self, provider, key, model, min_interval=3):
        self.provider, self.key, self.model = provider, key, model
        self.http = httpx.AsyncClient(timeout=60)
        self.throttle = Throttle(min_interval)

    async def _ask(self, system, user):
        await self.throttle.wait()
        if self.provider == "deepseek":
            r = await self.http.post("https://api.deepseek.com/chat/completions",
                                     headers={"Authorization": f"Bearer {self.key}"},
                                     json={"model": self.model, "temperature": 0.1,
                                           "response_format": {"type": "json_object"},
                                           "messages": [{"role": "system", "content": system},
                                                        {"role": "user", "content": user}]})
            if r.status_code != 200:
                raise AIError(f"DeepSeek {r.status_code}: {r.text[:150]}")
            return parse_json(r.json()["choices"][0]["message"]["content"])
        r = await self.http.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.key},
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                                       "maxOutputTokens": 2500}})
        if r.status_code != 200:
            raise AIError(f"Gemini {self.model} {r.status_code}: {r.text[:150]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return parse_json("".join(p.get("text", "") for p in parts))

    async def review(self, source, draft):
        """Returns (draft, info). info = {"status": ok|fixed|error, "issues": [...], "by": name}."""
        by = f"{self.provider}:{self.model}"
        try:
            res = await self._ask(REVIEW_SYSTEM, review_user(source, draft))
        except (AIError, httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            log.warning("review failed: %s", e)
            return draft, {"status": "error", "issues": [str(e)[:120]], "by": by}
        issues = [str(i).strip() for i in (res.get("issues") or []) if str(i).strip()][:5]
        fixed = res.get("fixed")
        if res.get("ok") and not issues:
            return draft, {"status": "ok", "issues": [], "by": by}
        if isinstance(fixed, dict):
            try:
                merged = validate({**draft, **fixed, "is_news": True})
                return merged, {"status": "fixed", "issues": issues or ["تعديلات صياغة"], "by": by}
            except ValueError as e:
                issues.append(f"التصحيح ناقص ({e}) — بقيت المسودة الأصلية")
        return draft, {"status": "flagged", "issues": issues or ["ملاحظة بدون تفاصيل"], "by": by}
