"""Load gateway configuration and resolve model profiles."""
import os
import re
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

# 档案里允许直接写 config.yaml 的连接字段
FLAT_MODEL_KEYS = ("base_url", "api_key", "model", "max_tokens",
                   "temperature", "timeout_s", "stream_read_s", "stream")


def _resolve_path(value, base: Path) -> str:
    """Resolve portable relative paths while preserving explicit user paths."""
    path = Path(str(value)).expanduser()
    return str(path if path.is_absolute() else (base / path).resolve())


def load() -> dict:
    with open(BASE_DIR / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    tools = cfg.setdefault("tools", {})
    if tools.get("workspace"):
        tools["workspace"] = _resolve_path(tools["workspace"], BASE_DIR)
    if tools.get("command"):
        tools["command"] = _resolve_path(tools["command"], BASE_DIR)
        tools["args"] = [_resolve_path(a, BASE_DIR) if str(a).endswith(".py") else str(a)
                         for a in tools.get("args", [])]
    raw_model = cfg.get("model", {}) or {}
    if raw_model.get("profiles"):
        # 全档案解析表（env 覆盖后），供工作流节点按档案选用模型
        cfg["model_profiles"] = {name: _apply_env(dict(p))
                                 for name, p in raw_model["profiles"].items()
                                 if isinstance(p, dict)}
    else:
        cfg["model_profiles"] = {"primary": _apply_env(dict(raw_model))}
    cfg["model"] = _resolve_model(raw_model)
    return cfg


TOOL_SURFACES = ("meta", "native")


def _profile_public(p: dict) -> dict:
    key = str(p.get("api_key", ""))
    return {"base_url": p.get("base_url", ""),
            "api_key": "*" * min(8, len(key)) if key else "",
            "model": p.get("model", ""),
            "max_tokens": p.get("max_tokens", 4000),
            "temperature": p.get("temperature", 0.6),
            "timeout_s": p.get("timeout_s", 600),
            "stream_read_s": p.get("stream_read_s", 120),
            "stream": bool(p.get("stream", True))}


def public_settings(cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    model = cfg.get("model", {})
    key = str(model.get("api_key", ""))
    agent = cfg.get("agent", {})
    server = cfg.get("server", {})
    return {
        "model": {**_profile_public(model),
                  "active": model.get("profile_name", "primary"),
                  "failover": [f.get("profile_name") for f in model.get("fallbacks", [])
                               if f.get("profile_name")],
                  "profiles": [{"id": name, **_profile_public(p)}
                               for name, p in cfg.get("model_profiles", {}).items()]},
        "agent": {"max_iterations": agent.get("max_iterations", 40),
                  "context_budget_est": agent.get("context_budget_est", 64000),
                  "system_prompt": agent.get("system_prompt", ""),
                  "tool_surface": agent.get("tool_surface", "native"),
                  "tool_call_compat": bool(agent.get("tool_call_compat", False))},
        "server": {"host": server.get("host", "127.0.0.1"),
                   "port": server.get("port", 8300)},
    }


def update_settings(values: dict) -> dict:
    path = BASE_DIR / "config.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    mraw = raw.setdefault("model", {})
    profiles = mraw.setdefault("profiles", {})
    # 旧版扁平 model 段一次性迁移进 primary 档案
    if not profiles:
        profiles["primary"] = {k: mraw[k] for k in FLAT_MODEL_KEYS if k in mraw}
        for k in FLAT_MODEL_KEYS:
            mraw.pop(k, None)
    if mraw.get("active") not in profiles:
        mraw["active"] = next(iter(profiles))
    incoming = values.get("model", {})

    # ── 档案管理（CCswitch 式：保存档案 / 删除 / 切换当前 / 故障切换顺序）──
    prof = incoming.get("save_profile")
    if isinstance(prof, dict) and str(prof.get("id", "")).strip():
        pid = str(prof["id"]).strip()
        if not re.fullmatch(r"[\w.-]{1,64}", pid):
            raise ValueError(f"档案名不合法: {pid!r}（仅限字母/数字/._-，最长 64）")
        # 未提供的字段沿用旧档案；密钥传掩码或空 = 保留原值
        item = {k: profiles[pid][k] for k in FLAT_MODEL_KEYS if k in profiles.get(pid, {})}
        for k in ("base_url", "model"):
            if prof.get(k):
                item[k] = prof[k]
        if prof.get("api_key") and not str(prof["api_key"]).startswith("*"):
            item["api_key"] = prof["api_key"]
        for k, cast in (("max_tokens", int), ("temperature", float), ("timeout_s", int),
                        ("stream_read_s", float)):
            if prof.get(k) not in (None, ""):
                item[k] = cast(prof[k])
        if "stream" in prof:
            item["stream"] = bool(prof["stream"])
        profiles[pid] = item
    del_id = incoming.get("delete_profile")
    if del_id and del_id in profiles:
        if len(profiles) <= 1:
            raise ValueError("至少保留一个模型档案，不能删除")
        del profiles[del_id]
        mraw["failover"] = [n for n in mraw.get("failover", []) if n in profiles]
        if mraw.get("active") == del_id:
            mraw["active"] = next(iter(profiles))
    if incoming.get("active") in profiles:
        mraw["active"] = incoming["active"]
    if isinstance(incoming.get("failover"), list):
        seen, fo = set(), []
        for n in incoming["failover"]:
            if n in profiles and n != mraw["active"] and n not in seen:
                seen.add(n)
                fo.append(n)
        mraw["failover"] = fo

    # ── 旧版扁平字段 → 写进当前 active 档案（原实现硬编码 primary，已修）──
    cur = profiles[mraw["active"]]
    for key in ("base_url", "model", "max_tokens", "temperature", "timeout_s", "stream_read_s", "stream"):
        if key in incoming and incoming[key] not in (None, ""):
            cur[key] = incoming[key]
    if incoming.get("api_key") and not str(incoming["api_key"]).startswith("*"):
        cur["api_key"] = incoming["api_key"]
    server = raw.setdefault("server", {})
    for key in ("host", "port"):
        if values.get("server", {}).get(key) not in (None, ""):
            server[key] = values["server"][key]
    agent = raw.setdefault("agent", {})
    for key in ("max_iterations", "context_budget_est"):
        if key in values.get("agent", {}): agent[key] = values["agent"][key]
    # 系统提示词允许清空（空串 = 回到内置默认）
    if "system_prompt" in values.get("agent", {}):
        agent["system_prompt"] = str(values["agent"]["system_prompt"] or "")
    surface = values.get("agent", {}).get("tool_surface")
    if "tool_call_compat" in values.get("agent", {}):
        agent["tool_call_compat"] = bool(values["agent"]["tool_call_compat"])
    if surface in TOOL_SURFACES:
        agent["tool_surface"] = surface
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    return load()


def _resolve_model(model_cfg: dict) -> dict:
    """Support legacy flat config and profile-based config with env overrides."""
    if "profiles" not in model_cfg:
        return _apply_env(dict(model_cfg))

    profiles = model_cfg["profiles"]
    active_name = model_cfg.get("active", "primary")
    if active_name not in profiles:
        raise ValueError(f"unknown active model profile: {active_name}")

    active = _apply_env(dict(profiles[active_name]))
    fallbacks = []
    for name in model_cfg.get("failover", []):
        if name not in profiles or name == active_name:
            continue
        item = _apply_env(dict(profiles[name]))
        item["profile_name"] = name
        fallbacks.append(item)
    active["profile_name"] = active_name
    active["fallbacks"] = fallbacks
    return active


def _apply_env(profile: dict) -> dict:
    """Environment values are intentionally not persisted to config.yaml."""
    env_map = {
        "base_url": "AGENT_MODEL_BASE_URL",
        "api_key": profile.get("api_key_env", "AGENT_MODEL_API_KEY"),
        "model": "AGENT_MODEL_NAME",
        "max_tokens": "AGENT_MODEL_MAX_TOKENS",
        "temperature": "AGENT_MODEL_TEMPERATURE",
        "timeout_s": "AGENT_MODEL_TIMEOUT_S",
    }
    for key, env_name in env_map.items():
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        if key == "max_tokens":
            profile[key] = int(value)
        elif key in ("temperature", "timeout_s"):
            profile[key] = float(value) if key == "temperature" else int(value)
        else:
            profile[key] = value
    profile.pop("api_key_env", None)
    return profile
