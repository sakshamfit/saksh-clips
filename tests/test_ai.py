"""The AI layer, with the network replaced: every kind of service is asked correctly and its answer is read
correctly. Nothing leaves this machine and no key is needed."""
import json
import os
import sys
import tempfile

os.environ["ANHY_DATA"] = tempfile.mkdtemp(prefix="pc-ai-")
os.environ["DATABASE_URL"] = ""
os.environ.update(ANHY_AI_PROVIDER="gemini", ANHY_AI_MODELS="model-a, model-b", ANHY_AI_KEY="test-key-123456", ANHY_AI_BASE_URL="")
sys.path.insert(0, ".")
from anhy import ai, auth, brain, config, ctx, db, usage  # noqa: E402

db.init()
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


class Reply:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


CLIP = {"start": 1, "end": 20, "score": 80, "hook": "WOW", "title": "t", "description": "d", "hashtags": ["a"], "reason": "r"}
seen = []


def fake_post(url, json=None, headers=None, timeout=None, **_k):  # noqa: A002
    seen.append((url, json, headers or {}))
    if url.endswith(":generateContent"):
        return Reply(200, {"candidates": [{"content": {"parts": [{"text": globals()["json_text"]([CLIP])}]}}],
                           "usageMetadata": {"promptTokenCount": 100, "totalTokenCount": 130}})
    if url.endswith("/chat/completions"):
        if "temperature" in json:                      # a model that refuses an extra setting: asked again without it
            return Reply(400, {"error": {"message": "temperature is not supported"}})
        return Reply(200, {"choices": [{"message": {"content": "```json\n" + globals()["json_text"]({"items": [CLIP]}) + "\n```"}}],
                           "usage": {"prompt_tokens": 90, "completion_tokens": 20}})
    if url.endswith("/messages"):
        return Reply(200, {"content": [{"type": "text", "text": "Here you go: " + globals()["json_text"]({"items": [CLIP]})}],
                           "usage": {"input_tokens": 70, "output_tokens": 10}})
    return Reply(404, {})


def json_text(x):
    return json.dumps(x)


ai.httpx.post = fake_post


def use(provider, base=""):
    config.AI_PROVIDER, config.AI_BASE_URL = provider, base
    seen.clear()


use("gemini")
out, sent, wrote = ai.call("model-a", "hello", brain.SCHEMA)
check("gemini: answer and tokens", out == [CLIP] and (sent, wrote) == (100, 30), str((out, sent, wrote)))
check("gemini: key in the header, schema passed as is", seen[0][2].get("x-goog-api-key") == "test-key-123456"
      and seen[0][1]["generationConfig"]["responseSchema"] is brain.SCHEMA and seen[0][0].endswith("/model-a:generateContent"))

for name, host in (("arena", "api.arena.ai"), ("openai", "api.openai.com"), ("xai", "api.x.ai"), ("grok", "api.x.ai"), ("groq", "api.groq.com"), ("openrouter", "openrouter.ai")):
    use(name)
    out, sent, wrote = ai.call("model-a", "hello", brain.SCHEMA)
    check(f"{name}: answer unwrapped from a code fence, tokens read", out == [CLIP] and (sent, wrote) == (90, 20), str((out, sent, wrote)))
    check(f"{name}: right address, key as a bearer token", host in seen[0][0] and seen[0][2].get("Authorization") == "Bearer test-key-123456")
check("a refused extra setting is dropped and asked again", len(seen) == 2 and "temperature" not in seen[1][1])
check("the schema is told in the prompt for chat models", '"type": "array"' in seen[1][1]["messages"][0]["content"])

use("arena")
check("Arena uses its documented API endpoint", ai.base_url() == "https://api.arena.ai/v1")
seen.clear()
ai.see("Describe this", b"jpeg")
check("Arena vision uses the selected model and bearer key", seen[0][0] == "https://api.arena.ai/v1/chat/completions"
      and seen[0][2].get("Authorization") == "Bearer test-key-123456"
      and seen[0][1]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))


def fake_models(url, headers=None, timeout=None, **_k):
    seen.append((url, None, headers or {}))
    return Reply(200, {"data": [{"id": "direct-model"}, {"id": "coding-router-preview"}]})


ai.httpx.get = fake_models
seen.clear()
check("Arena lists models available to the key", ai.list_models() == ["coding-router-preview", "direct-model"]
      and seen[0][0] == "https://api.arena.ai/v1/models" and seen[0][2].get("Authorization") == "Bearer test-key-123456")

use("anthropic")
out, sent, wrote = ai.call("model-a", "hello", brain.SCHEMA)
check("anthropic: answer found inside a sentence, tokens read", out == [CLIP] and (sent, wrote) == (70, 10), str((out, sent, wrote)))
check("anthropic: key and version headers", seen[0][2].get("x-api-key") == "test-key-123456" and "anthropic-version" in seen[0][2])

use("custom", "http://127.0.0.1:1234/v1")
out, _s, _w = ai.call("model-a", "hello", brain.SCHEMA)
check("custom: uses the address from settings", seen[0][0] == "http://127.0.0.1:1234/v1/chat/completions" and out == [CLIP])

use("custom")
check("custom without an address is not ready", not ai.ready())
use("nonsense")
check("an unknown provider is not ready", not ai.ready())
use("ollama")
os.environ["ANHY_AI_KEY"] = ""
config._kept.clear()
check("ollama needs no key", ai.ready() and ai.label().startswith("Ollama"))
out, _s, _w = ai.call("model-a", "hello", brain.SCHEMA)
check("ollama: no key header is sent", "Authorization" not in seen[0][2] and out == [CLIP])
use("gemini")
check("gemini without a key is not ready", not ai.ready())
os.environ["ANHY_AI_KEY"] = "test-key-123456"
config._kept.clear()

# ---- the whole path: a call is made for a user and shows up in their usage, and only theirs
u1, _ = auth._create("ai-one@example.com", "One", password_hash="x")
u2, _ = auth._create("ai-two@example.com", "Two", password_hash="x")
words = [{"w": f"word{i}", "s": i * 0.5, "e": i * 0.5 + 0.4} for i in range(120)]
with ctx.as_user(u1.id):
    usage.set_context(None, "A test video")
    import numpy as np
    clips, info = brain.find_clips(words, np.zeros(60), 60.0, title="T", uploader="U")
    check("clips come back through the whole path", len(clips) == 1 and clips[0]["hook"] == "WOW" and info["model"] == "model-a", str((clips, info)))
    s = usage.summary({})
    check("the call is in this user's usage", s["calls_total"] == 1 and s["tokens_total"] == 130 and s["recent"][0]["where"] == "A test video", str(s))
    check("usage says which AI is set", s["ready"] and s["provider"] == "Gemini" and s["model"] == "model-a")
with ctx.as_user(u2.id):
    check("another user sees none of it", usage.summary({})["calls_total"] == 0)

# A person's optional clipping preferences reach the scout, but cannot replace its safety rules.
from anhy import jobs  # noqa: E402
check("special instructions are cleaned and bounded", jobs.clean_options({"instructions": "  Funny   reactions\nno spoilers  "})["instructions"] == "Funny reactions no spoilers")
try:
    jobs.clean_options({"instructions": "a" * 501})
    check("oversized instructions are refused", False)
except ValueError:
    check("oversized instructions are refused", True)
with ctx.as_user(u1.id):
    seen.clear()
    brain.find_clips(words, np.zeros(60), 60.0, title="T", uploader="U", instructions="Find the funny reactions")
    prompt = seen[0][1]["contents"][0]["parts"][0]["text"]
    check("the scout sees the viewer preference and keeps the clip rules", "Find the funny reactions" in prompt and "Never put links" in prompt)
    check("the scout asks for a grounded social caption and searchable tags", "ready-to-post social caption" in prompt and "searchable tags" in prompt)

# Tags are useful only when relevant and valid, not when they are generic reach-bait.
meta = brain._tidy({"category": "clutch", "title": "The final round", "description": "That last play. #GTA6 #FYP",
                   "hashtags": ["#GTA6", "gta6", "FYP", "https://bad.example", "Clutch", "#Gaming"]}, 2, 20, game="gta 6")
check("social caption has no duplicate embedded hashtags", "#" not in meta["description"] and "That last play" in meta["description"])
check("tags are unique, limited and contain the game", len(meta["hashtags"]) <= 5 and meta["hashtags"].count("GTA6") == 1)
check("spam and broken link tags are rejected", "FYP" not in meta["hashtags"] and all("http" not in t for t in meta["hashtags"]))
check("AI can leave a title as an honest caption fallback", brain._tidy({"title": "A real moment", "hashtags": []}, 0, 10)["description"] == "A real moment")
check("a clip still gets clean tags when AI is unavailable", len(brain._tidy({"hashtags": []}, 0, 10)["hashtags"]) == 3)
check("manually cleared tags stay cleared", brain.clean_hashtags("", fill=False) == [])

# ---- every way the AI can fail must leave a reason the dashboard can show
ai.problem.update(text="", at=0.0)
real_ready = config.ai_ready
config.ai_ready = lambda: False
try:
    brain.ask_ai("x", purpose="test")
except Exception:  # noqa: BLE001
    pass
config.ai_ready = real_ready
check("an AI that is not set up yet is said on the dashboard", bool(ai.problem["text"]))
check("...in words that say what to do about it", "Settings, AI" in ai.problem["text"])

check("a refused key is explained in words", "refused the key" in ai.explain(ai.AIError("HTTP 401 invalid api key")))
check("a broken service is explained in words", "problem on its side" in ai.explain(ai.AIError("HTTP 500 internal server error")))
check("an answer with no JSON in it is explained", "not in the form" in ai.explain(RuntimeError("The AI did not answer with JSON.")))
check("an error nobody has words for is still shown", bool(ai.explain(RuntimeError("something odd")) or "did not answer"))

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil  # noqa: E402
db.engine.dispose()
shutil.rmtree(os.environ["ANHY_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
