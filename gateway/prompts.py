"""提示词预设（CC Switch 式自选提示词）。

gateway/prompts.json 存 [{id, name, content}]；默认为空，预设全部由用户在前端自建。
content 会作为附加 system prompt 拼接（不替换基础 SYSTEM_PROMPT）。
"""
import json
import uuid
from pathlib import Path

PROMPTS_FILE = Path(__file__).resolve().parent / "prompts.json"

SEED: list = []


def load() -> list:
    if not PROMPTS_FILE.exists():
        save(SEED)
        return SEED
    return json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))


def save(prompts: list) -> list:
    """规整后落盘并返回：只留 id/name/content，补 id，丢掉空名。"""
    clean = []
    for p in prompts:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        clean.append({"id": str(p.get("id") or uuid.uuid4().hex[:8]),   # 补 id，防止前端新增项漏带
                      "name": name[:60], "content": str(p.get("content") or "")})
    PROMPTS_FILE.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def get(prompt_id: str) -> dict | None:
    if not prompt_id:
        return None
    return next((p for p in load() if p["id"] == prompt_id), None)
