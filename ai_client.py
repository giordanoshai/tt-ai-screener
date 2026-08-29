"""
Multi-format AI client — supports OpenAI and Anthropic API formats.
All functions accept model config dict with api_format field.
"""
import json
import os
from pathlib import Path

import requests

import config


# ── OpenAI format ────────────────────────────────────────────────────────────

def _openai_chat(cfg: dict, messages: list[dict],
                 temperature: float = 0.3, max_tokens: int = 1024) -> str | None:
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    resp = requests.post(
        f"{cfg['api_base'].rstrip('/')}/chat/completions",
        headers=headers,
        json={"model": cfg["model_id"], "messages": messages,
              "temperature": temperature, "max_tokens": max_tokens},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _openai_stream(cfg: dict, messages: list[dict],
                   temperature: float = 0.3, max_tokens: int = 4096):
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    resp = requests.post(
        f"{cfg['api_base'].rstrip('/')}/chat/completions",
        headers=headers,
        json={"model": cfg["model_id"], "messages": messages,
              "temperature": temperature, "max_tokens": max_tokens, "stream": True},
        timeout=120, stream=True,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        text = line.decode("utf-8")
        if not text.startswith("data: "):
            continue
        data = text[6:]
        if data.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(data)
            delta = chunk["choices"][0].get("delta", {})
            reasoning = delta.get("reasoning_content")
            content = delta.get("content")
            if reasoning:
                yield {"type": "thinking", "chunk": reasoning}
            if content:
                yield {"type": "content", "chunk": content}
        except (json.JSONDecodeError, KeyError, IndexError):
            continue


# ── Anthropic format ─────────────────────────────────────────────────────────

def _anthropic_chat(cfg: dict, messages: list[dict],
                    temperature: float = 0.3, max_tokens: int = 1024) -> str | None:
    system_msg = ""
    api_messages = []
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        else:
            api_messages.append({"role": m["role"], "content": m["content"]})

    body = {
        "model": cfg["model_id"],
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_msg:
        body["system"] = system_msg
    if cfg.get("supports_thinking"):
        body["thinking"] = {"type": "enabled", "budget_tokens": min(max_tokens, 8192)}

    resp = requests.post(
        f"{cfg['api_base'].rstrip('/')}/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": cfg.get("api_key", ""),
            "anthropic-version": "2023-06-01",
        },
        json=body, timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            parts.append(block["text"])
    return "".join(parts) if parts else None


def _anthropic_stream(cfg: dict, messages: list[dict],
                      temperature: float = 0.3, max_tokens: int = 4096):
    system_msg = ""
    api_messages = []
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        else:
            api_messages.append({"role": m["role"], "content": m["content"]})

    body = {
        "model": cfg["model_id"],
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if system_msg:
        body["system"] = system_msg
    if cfg.get("supports_thinking"):
        body["thinking"] = {"type": "enabled", "budget_tokens": min(max_tokens, 8192)}

    resp = requests.post(
        f"{cfg['api_base'].rstrip('/')}/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": cfg.get("api_key", ""),
            "anthropic-version": "2023-06-01",
        },
        json=body, timeout=120, stream=True,
    )
    resp.raise_for_status()
    current_type = "content"
    for line in resp.iter_lines():
        if not line:
            continue
        text = line.decode("utf-8")
        if not text.startswith("data: "):
            continue
        data = text[6:]
        if data.strip() == "[DONE]":
            break
        try:
            event = json.loads(data)
            event_type = event.get("type", "")

            if event_type == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "thinking":
                    current_type = "thinking"
                else:
                    current_type = "content"

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type", "")
                if delta_type == "thinking_delta":
                    yield {"type": "thinking", "chunk": delta.get("thinking", "")}
                elif delta_type == "text_delta":
                    yield {"type": "content", "chunk": delta.get("text", "")}

        except (json.JSONDecodeError, KeyError):
            continue


# ── Unified dispatch ─────────────────────────────────────────────────────────

def _chat(model_cfg: dict, messages: list[dict],
          temperature: float = 0.3, max_tokens: int = 1024) -> str | None:
    if not model_cfg or not model_cfg.get("api_base") or not model_cfg.get("model_id"):
        return None
    fmt = model_cfg.get("api_format", "openai")
    if fmt == "anthropic":
        return _anthropic_chat(model_cfg, messages, temperature, max_tokens)
    return _openai_chat(model_cfg, messages, temperature, max_tokens)


def chat_stream(model_cfg: dict, messages: list[dict],
                temperature: float = 0.3, max_tokens: int = 4096):
    if not model_cfg or not model_cfg.get("api_base") or not model_cfg.get("model_id"):
        return
    fmt = model_cfg.get("api_format", "openai")
    if fmt == "anthropic":
        yield from _anthropic_stream(model_cfg, messages, temperature, max_tokens)
    else:
        yield from _openai_stream(model_cfg, messages, temperature, max_tokens)


# ── 模型配置：读本地 JSON 文件 ────────────────────────────────────────────
#
# 这套配置原来存在本地 DuckDB 的 ai_models 表里。2026-08-29 实测：迁到自建
# Postgres 的时候这张表漏掉了，线上库里根本没有，所以 get_model() 永远返回
# None —— 网页里的 AI 分析完全不能用，界面上还只显示「没配置模型」，会让人
# 以为是自己配错了；那套增删改模型的表单也存不进去，填完就消失。
#
# 现在改成读 config/models.json：九个模型（不同厂商、不同价位，分析和情绪
# 打分各用各的）都在里面，改模型直接改这个文件。
#
# **密钥不在 JSON 里。** 每个模型带一个 api_key_env 字段，写的是环境变量的
# 名字（ANTHROPIC_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY / AI_API_KEY），
# 真实的值在 .env。这样 JSON 可以进版本库而不泄密。
#
# 环境变量那一套（AI_API_BASE / AI_MODEL 等）保留成兜底，只在 JSON 文件不
# 存在、读不动或者里面一个模型都没有的时候才走。
#
# 文件每次都重新读，不做缓存：它只有几 KB，而改完能立刻生效比省这点开销值。

CONFIG_DIR = Path(__file__).parent / "config"

#: 模型清单文件。测试里会把这个名字指到临时文件上。
MODELS_JSON = CONFIG_DIR / "models.json"

#: JSON 里没写 role 时按哪个角色算。
DEFAULT_ROLE = "analysis"

_ROLES = ("analysis", "sentiment")

# 兜底用：每个角色对应 config.py 里的哪三个变量。
_ROLE_ENV = {
    "analysis": ("AI_API_BASE", "AI_API_KEY", "AI_MODEL", "分析模型"),
    "sentiment": ("SENTIMENT_API_BASE", "SENTIMENT_API_KEY", "SENTIMENT_MODEL",
                  "情绪打分模型"),
}


def _load_models_file() -> list[dict]:
    """读模型清单。文件缺失、读不动、格式不对都返回空列表，不抛异常。"""
    try:
        raw = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = raw.get("models") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []
    return [m for m in entries if isinstance(m, dict) and m.get("id")]


def _enabled_models() -> list[dict]:
    """启用中的模型。enabled 没写按启用算，写了 false 就不出现在任何地方。"""
    return [m for m in _load_models_file() if m.get("enabled", True)]


def _api_key_of(model: dict) -> str:
    """按 api_key_env 指的名字去环境里取密钥。没配就是空串，不报错。"""
    env_name = (model.get("api_key_env") or "").strip()
    return os.getenv(env_name, "") if env_name else ""


def _as_config(model: dict) -> dict:
    """把 JSON 里的一条变成调用方要的配置（这一步才把密钥填进去）。"""
    return {
        "id": model["id"],
        "display_name": model.get("display_name") or model["id"],
        "api_base": model.get("api_base", ""),
        "api_key": _api_key_of(model),
        "model_id": model.get("model_id", ""),
        "api_format": model.get("api_format", "openai"),
        "role": model.get("role", DEFAULT_ROLE),
        "supports_thinking": bool(model.get("supports_thinking")),
    }


def _serves(model: dict, role: str) -> bool:
    """这个模型能不能干这个角色的活。role=both 的两边都能干。"""
    return model.get("role", DEFAULT_ROLE) in (role, "both")


def _model_from_env(role: str) -> dict | None:
    """兜底：从环境变量拼一个模型配置。没配返回 None。"""
    base_k, key_k, model_k, label = _ROLE_ENV.get(role, _ROLE_ENV[DEFAULT_ROLE])
    api_base = getattr(config, base_k, "")
    model_id = getattr(config, model_k, "")
    if not api_base or not model_id:
        return None
    return {
        "id": role,
        "display_name": "%s（%s）" % (label, model_id),
        "api_base": api_base,
        "api_key": getattr(config, key_k, ""),
        "model_id": model_id,
        "api_format": "openai",
        "role": role,
        "supports_thinking": False,
    }


def get_model(model_id: str = None, role: str = "analysis") -> dict | None:
    """取一个模型配置。没配返回 None，上层显示「未配置」。

    指定了 `model_id` 就用那一个；那个 id 不在清单里（比如网页上选过之后
    配置被删了）就退回按角色取默认的，不要让整个分析都用不了。
    """
    role = role if role in _ROLES else DEFAULT_ROLE
    models = _enabled_models()
    if not models:
        return _model_from_env(role)

    if model_id:
        for model in models:
            if model["id"] == model_id:
                return _as_config(model)
        # 兼容旧写法：曾经把角色名当 model_id 传进来
        if model_id in _ROLES:
            role = model_id

    usable = [m for m in models if _serves(m, role)]
    if not usable:
        return None

    flag = "is_default_analysis" if role == "analysis" else "is_default_sentiment"
    for model in usable:
        if model.get(flag):
            return _as_config(model)
    return _as_config(usable[0])


def list_models(role: str = None) -> list[dict]:
    """列出可用的模型，给设置页和 /analyze/status 用。

    **不带密钥**，只带一个 has_key 说明那个环境变量配没配。
    """
    models = _enabled_models()
    if not models:
        cfgs = [_model_from_env(r) for r in (
            [role] if role in _ROLES else list(_ROLES))]
        return [{
            "id": c["id"], "display_name": c["display_name"],
            "api_base": c["api_base"], "model_id": c["model_id"],
            "api_format": c["api_format"], "role": c["role"],
            "supports_thinking": c["supports_thinking"],
            "is_default_sentiment": c["role"] == "sentiment",
            "is_default_analysis": c["role"] == "analysis",
            "has_key": bool(c["api_key"]),
        } for c in cfgs if c]

    out = []
    for model in models:
        if role and not _serves(model, role):
            continue
        out.append({
            "id": model["id"],
            "display_name": model.get("display_name") or model["id"],
            "api_base": model.get("api_base", ""),
            "model_id": model.get("model_id", ""),
            "api_format": model.get("api_format", "openai"),
            "role": model.get("role", DEFAULT_ROLE),
            "supports_thinking": bool(model.get("supports_thinking")),
            "is_default_sentiment": bool(model.get("is_default_sentiment")),
            "is_default_analysis": bool(model.get("is_default_analysis")),
            "has_key": bool(_api_key_of(model)),
        })
    return out


def analysis_available() -> bool:
    return get_model(role="analysis") is not None


def sentiment_available() -> bool:
    return get_model(role="sentiment") is not None


# ── Sentiment scoring ────────────────────────────────────────────────────────

SENTIMENT_SYSTEM_PROMPT = """You are a financial news sentiment analyzer.
For each news headline+summary, output a JSON object with:
- "label": one of "positive", "negative", "neutral"
- "score": float from -1.0 (very negative) to 1.0 (very positive)

Be concise. Only output the JSON array, no explanation."""


def score_news_batch(articles: list[dict]) -> list[dict]:
    model_cfg = get_model(role="sentiment")
    if not model_cfg or not articles:
        return [{"label": None, "score": None}] * len(articles)

    lines = []
    for i, art in enumerate(articles):
        headline = art.get("headline", "")
        summary = art.get("summary", "")[:200]
        lines.append(f"{i+1}. {headline}" + (f" — {summary}" if summary else ""))

    user_msg = "Score these financial news items:\n" + "\n".join(lines)
    user_msg += f'\n\nReturn a JSON array of {len(articles)} objects, each with "label" and "score".'

    try:
        raw = _chat(model_cfg, [
            {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ], max_tokens=2048)
        if not raw:
            return [{"label": None, "score": None}] * len(articles)

        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if "<think>" in text:
            text = text.split("</think>")[-1].strip()
        results = json.loads(text)
        if isinstance(results, list) and len(results) == len(articles):
            return results
    except Exception:
        pass
    return [{"label": None, "score": None}] * len(articles)


# ── Sentiment Batch Inference (Aliyun Bailian / OpenAI Batch API) ─────────────

def submit_news_sentiment_batch(articles: list[dict]) -> str | None:
    """Submit news sentiment tasks to Aliyun Bailian / OpenAI Batch API.

    Returns the created batch_id, or None if submission failed.
    """
    import time
    model_cfg = get_model(role="sentiment")
    if not model_cfg or not model_cfg.get("api_base") or not model_cfg.get("model_id"):
        return None

    api_base = model_cfg["api_base"].rstrip("/")
    api_key = model_cfg.get("api_key", "")
    model_id = model_cfg["model_id"]

    # Aliyun Bailian Batch API does not support deepseek-v4-flash yet.
    # Fallback to qwen-plus which is fully supported and highly accurate.
    if model_id == "deepseek-v4-flash":
        model_id = "qwen-plus"

    headers = {"Authorization": f"Bearer {api_key}"}

    # 1. Generate JSONL content locally
    # Limiting summary length to avoid token waste
    batch_lines = []
    for art in articles:
        news_id = art["id"]
        headline = art.get("headline", "")
        summary = art.get("summary", "")[:300]
        body_text = f"{headline} — {summary}" if summary else headline

        req_item = {
            "custom_id": f"news_{news_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model_id,
                "messages": [
                    {"role": "system", "content": "You are a financial news sentiment analyzer. Output JSON only: {\"label\": \"positive\"|\"negative\"|\"neutral\", \"score\": float(-1.0 to 1.0)}"},
                    {"role": "user", "content": body_text}
                ],
                "temperature": 0.1,
                # Explicitly disable Qwen thinking mode to save tokens/cost
                "enable_thinking": False
            }
        }
        batch_lines.append(json.dumps(req_item, ensure_ascii=False))

    temp_filename = f"temp_batch_{int(time.time())}.jsonl"
    with open(temp_filename, "w", encoding="utf-8") as f:
        f.write("\n".join(batch_lines) + "\n")

    try:
        # 2. Upload file to Bailian (POST /files)
        # Using with block to ensure file handle is closed properly (prevents WinError 32)
        with open(temp_filename, "rb") as file_stream:
            files = {"file": (temp_filename, file_stream, "application/octet-stream")}
            data = {"purpose": "batch"}
            r = requests.post(f"{api_base}/files", headers=headers, files=files, data=data, timeout=30)
            r.raise_for_status()
            file_info = r.json()
            file_id = file_info["id"]

        # 3. Create batch job (POST /batches)
        batch_data = {
            "input_file_id": file_id,
            "endpoint": "/v1/chat/completions",
            "completion_window": "24h",
            "metadata": {
                "ds_name": f"sentiment_batch_{int(time.time())}",
                "ds_description": f"Batch news sentiment scoring for {len(articles)} articles"
            }
        }
        r = requests.post(f"{api_base}/batches", headers=headers, json=batch_data, timeout=30)
        r.raise_for_status()
        job_info = r.json()
        return job_info["id"]

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to submit Aliyun Bailian Batch: {e}")
        return None
    finally:
        # Clean up local temp file
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except Exception:
                pass


def check_batch_status(batch_id: str) -> dict:
    """Retrieve status details for a Bailian batch job.

    Returns the JSON dict from API response.
    """
    model_cfg = get_model(role="sentiment")
    if not model_cfg or not model_cfg.get("api_base"):
        return {}

    api_base = model_cfg["api_base"].rstrip("/")
    api_key = model_cfg.get("api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        r = requests.get(f"{api_base}/batches/{batch_id}", headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to retrieve status for batch {batch_id}: {e}")
        return {}


def download_batch_results(output_file_id: str) -> list[dict]:
    """Download results file from Bailian and parse them.

    Returns a list of parsed dicts: [{"news_id": int, "label": str, "score": float}]
    """
    model_cfg = get_model(role="sentiment")
    if not model_cfg or not model_cfg.get("api_base"):
        return []

    api_base = model_cfg["api_base"].rstrip("/")
    api_key = model_cfg.get("api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"}

    results = []
    try:
        r = requests.get(f"{api_base}/files/{output_file_id}/content", headers=headers, timeout=60)
        r.raise_for_status()
        
        # Each line is a JSON: {"custom_id": "news_123", "response": {"status_code": 200, "body": {"choices": [{"message": {"content": "..."}}]}}}
        for line in r.text.strip().split("\n"):
            if not line.strip():
                continue
            item = json.loads(line)
            custom_id = item.get("custom_id", "")
            if not custom_id.startswith("news_"):
                continue
            
            try:
                news_id = int(custom_id.split("_")[1])
            except ValueError:
                continue

            resp_body = item.get("response", {}).get("body", {})
            choices = resp_body.get("choices", [])
            if not choices:
                continue
            
            content = choices[0].get("message", {}).get("content", "").strip()
            # Clean markdown code block wraps
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            if "<think>" in content:
                content = content.split("</think>")[-1].strip()

            try:
                sentiment_data = json.loads(content)
                results.append({
                    "news_id": news_id,
                    "label": sentiment_data.get("label"),
                    "score": sentiment_data.get("score")
                })
            except Exception:
                pass

        return results
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to download and parse batch file {output_file_id}: {e}")
        return []

