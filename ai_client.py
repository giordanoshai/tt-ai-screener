"""
Multi-format AI client — supports OpenAI and Anthropic API formats.
All functions accept model config dict with api_format field.
"""
import json
import requests


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


# ── Model registry (reads from DB) ──────────────────────────────────────────

def get_model(model_id: str = None, role: str = "analysis") -> dict | None:
    from db.init import get_conn
    con = get_conn()
    if model_id:
        row = con.execute(
            "SELECT id, api_base, api_key, model_id, api_format, supports_thinking, display_name FROM ai_models WHERE id = ? AND enabled = TRUE",
            [model_id]
        ).fetchone()
    else:
        col = "is_default_sentiment" if role == "sentiment" else "is_default_analysis"
        row = con.execute(
            f"SELECT id, api_base, api_key, model_id, api_format, supports_thinking, display_name FROM ai_models WHERE {col} = TRUE AND enabled = TRUE LIMIT 1",
        ).fetchone()
    con.close()
    if not row:
        return None
    
    api_key = row[2]
    if not api_key:
        import os
        if role == "sentiment":
            api_key = os.getenv("SENTIMENT_API_KEY", "")
        else:
            api_key = os.getenv("AI_API_KEY", "")

    return {
        "id": row[0], "api_base": row[1], "api_key": api_key,
        "model_id": row[3], "api_format": row[4],
        "supports_thinking": row[5], "display_name": row[6],
    }


def list_models(role: str = None) -> list[dict]:
    from db.init import get_conn
    con = get_conn()
    where = "enabled = TRUE"
    params = []
    if role:
        where += " AND (role = ? OR role = 'both')"
        params.append(role)
    rows = con.execute(f"""
        SELECT id, display_name, model_id, api_format, role, supports_thinking,
               is_default_sentiment, is_default_analysis
        FROM ai_models WHERE {where} ORDER BY display_name
    """, params).fetchall()
    con.close()
    return [
        {"id": r[0], "display_name": r[1], "model_id": r[2], "api_format": r[3],
         "role": r[4], "supports_thinking": r[5],
         "is_default_sentiment": r[6], "is_default_analysis": r[7]}
        for r in rows
    ]


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
    import os
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

