"""AI providers behind one function: rewrite(text) -> dict.
Swap the model in .env (AI_PROVIDER) without touching the rest of the bot."""
import asyncio, json, logging, re, time
import httpx

from prompt import SCHEMA, system_prompt, user_prompt

log = logging.getLogger("llm")


class AIError(Exception):
    pass


def parse_json(txt):
    txt = (txt or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    try:
        return json.loads(txt)
    except ValueError:
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            return json.loads(m.group(0))
        raise AIError("الرد مو JSON")


class Throttle:
    """Keeps requests under the free-tier rate limit."""

    def __init__(self, min_interval):
        self.min, self.last, self.lock = min_interval, 0.0, asyncio.Lock()

    async def wait(self):
        async with self.lock:
            gap = self.min - (time.monotonic() - self.last)
            if gap > 0:
                await asyncio.sleep(gap)
            self.last = time.monotonic()


class Gemini:
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
    LIST = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"

    def __init__(self, key, models, style_path):
        self.key, self.models, self.style = key, models, style_path

    async def discover(self, client):
        """Ask Google which models this key can use; pick the newest Flash."""
        r = await client.get(self.LIST, headers={"x-goog-api-key": self.key})
        if r.status_code != 200:
            raise AIError(f"ما قدرت جيب قائمة الموديلات: {r.status_code} {r.text[:150]}")
        bad = ("image", "tts", "audio", "live", "embedding", "vision", "thinking", "exp", "learnlm", "robotics")
        found = []
        for m in r.json().get("models", []):
            name = m.get("name", "").split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []) or "flash" not in name:
                continue
            if any(b in name for b in bad):
                continue
            ver = [int(x) for x in re.findall(r"\d+", name.split("-flash")[0])] or [0]
            found.append((ver, "lite" not in name, "preview" not in name, name))
        found.sort(reverse=True)
        return [f[-1] for f in found]

    async def call(self, client, text, temperature, retry_note):
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt(self.style)}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt(text, retry_note)}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                                 "responseSchema": SCHEMA, "maxOutputTokens": 2000},
        }
        last, tried, busy, discovered = None, set(), [], False
        queue = list(self.models)
        while queue:
            m = queue.pop(0)
            if m in tried:
                continue
            tried.add(m)
            r = await client.post(self.URL.format(m=m), json=body, headers={"x-goog-api-key": self.key})
            if r.status_code in (404, 429, 500, 503):
                # 404 = retired / not for this key; 429/5xx = busy → move to the next model now
                (busy if r.status_code != 404 else []).append(f"{m}:{r.status_code}")
                last = f"{m}: {r.status_code}"
                log.info("model %s → %s, trying next", m, r.status_code)
                if not queue and not discovered and not getattr(self, "_discovered", False):
                    discovered = self._discovered = True
                    try:
                        queue = [x for x in await self.discover(client) if x not in tried][:4]
                        self.models = self.models + [x for x in queue if x not in self.models]
                        log.info("other models for this key: %s", queue)
                    except AIError as e:
                        log.warning("%s", e)
                continue
            if r.status_code != 200:
                raise AIError(f"{m}: {r.status_code} {r.text[:200]}")
            d = r.json()
            try:
                parts = d["candidates"][0]["content"]["parts"]
            except (KeyError, IndexError):
                raise AIError(f"رد فاضي: {str(d.get('promptFeedback') or d)[:200]}")
            if self.models[0] != m:              # remember the one that works
                self.models = [m] + [x for x in self.models if x != m]
                log.info("using model %s", m)
            return parse_json("".join(p.get("text", "") for p in parts))
        if busy:
            raise AIError("retry:" + " ".join(busy))
        raise AIError("ولا موديل شغّال: " + str(last) + " — جرّبت: " + ", ".join(sorted(tried)))


class DeepSeek:
    URL = "https://api.deepseek.com/chat/completions"

    def __init__(self, key, model, style_path):
        self.key, self.model, self.style = key, model, style_path

    async def call(self, client, text, temperature, retry_note):
        body = {"model": self.model, "temperature": temperature, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system_prompt(self.style) + "\nReturn JSON only."},
                             {"role": "user", "content": user_prompt(text, retry_note)}]}
        r = await client.post(self.URL, json=body, headers={"Authorization": f"Bearer {self.key}"})
        if r.status_code in (429, 500, 503):
            raise AIError(f"retry:{r.status_code}")
        if r.status_code != 200:
            raise AIError(f"{r.status_code} {r.text[:200]}")
        return parse_json(r.json()["choices"][0]["message"]["content"])


class Writer:
    def __init__(self, cfg):
        if cfg.PROVIDER == "deepseek":
            self.p = DeepSeek(cfg.DEEPSEEK_KEY, cfg.DEEPSEEK_MODEL, cfg.STYLE_PATH)
        else:
            self.p = Gemini(cfg.GEMINI_KEY, cfg.GEMINI_MODELS, cfg.STYLE_PATH)
        self.throttle = Throttle(cfg.MIN_INTERVAL)
        self.client = httpx.AsyncClient(timeout=60)

    async def rewrite(self, text, temperature=0.4, retry_note=None, tries=4):
        delay = 10
        for i in range(tries):
            await self.throttle.wait()
            try:
                return await self.p.call(self.client, text, temperature, retry_note)
            except AIError as e:
                if not str(e).startswith("retry:") or i == tries - 1:
                    raise
                log.warning("AI busy (%s), retry in %ss", e, delay)
                await asyncio.sleep(delay); delay *= 2
            except (httpx.HTTPError, ValueError) as e:
                if i == tries - 1:
                    raise AIError(f"شبكة: {e.__class__.__name__}")
                await asyncio.sleep(delay); delay *= 2
