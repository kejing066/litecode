"""lite config: OpenCode-style files, {env:VAR} substitution, model resolution.

Config files are OpenCode-style JSON or JSONC (comments, trailing commas):

- Global lite: ``~/.config/lite/opencode.json(c)`` (override the dir with
  ``LITECODE_CONFIG_DIR``).
- OpenCode-compat global: ``~/.config/opencode/opencode.json(c)``
  (override with ``LITECODE_OPENCODE_CONFIG_DIR``).
- Project: ``lite.json``, ``lite.jsonc``, ``opencode.json``,
  ``opencode.jsonc`` in the project directory (projects override global).

Model/provider selection mirrors OpenCode: the model reference is
``provider/model``, and ``providers`` is a map of provider id to entry with
``name``/``env``/``settings``/``models``. Lite adds a ``type`` field to each
provider entry (default ``"openai"``): ``openai`` (OpenAI-compatible chat
completions), ``codex`` (OpenAI Codex backend) or ``agy`` (Google Antigravity
CLI).
"""

from __future__ import annotations

import json
import os
import re
import sys

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_DEFAULT_MODEL = "gpt-5-codex"
AGY_DEFAULT_MODEL = "gemini-3-pro"

# Provider types supported as backends.
#   openai  - OpenAI-compatible chat completions (default)
#   codex   - OpenAI Codex backend (Responses API, chatgpt.com/backend-api/codex)
#   agy     - Google Antigravity CLI (agy -p, proxied)
BUILTIN_PROVIDERS = {
    "openai": {"type": "openai", "base_url": DEFAULT_BASE_URL},
    "codex": {"type": "codex", "base_url": CODEX_BASE_URL},
    "agy": {"type": "agy"},
}


def _strip_jsonc(text):
    """Strip // and /* */ comments and trailing commas, outside strings."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
        elif ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif ch == "/" and text[i : i + 2] == "//":
            j = text.find("\n", i)
            i = n if j == -1 else j
        elif ch == "/" and text[i : i + 2] == "/*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif ch == ",":
            # Drop commas that trail an object/array close (JSONC allows
            # trailing commas; json.loads does not).
            j = i + 1
            while j < n:
                if text[j].isspace():
                    j += 1
                elif text[j : j + 2] == "//":
                    k = text.find("\n", j)
                    j = n if k == -1 else k
                elif text[j : j + 2] == "/*":
                    k = text.find("*/", j + 2)
                    j = n if k == -1 else k + 2
                else:
                    break
            if j < n and text[j] in "}]":
                i = j
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def load_jsonc(path):
    """Load a JSONC config file; returns {} for empty files."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    text = _strip_jsonc(text)
    if not text.strip():
        return {}
    return json.loads(text)


_ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def _env_ref(m):
    return os.environ.get(m.group(1), "")


def _substitute_env(value):
    """Resolve {env:VAR} references, like OpenCode's config variables."""
    if isinstance(value, str):
        return _ENV_REF.sub(_env_ref, value)
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    return value


def _deep_merge(base, new):
    out = dict(base)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _config_paths(project_dir=None, global_dir=None):
    """Config files, lowest precedence first (lite merges later-wins)."""
    paths = []
    if global_dir is None:
        global_dir = os.environ.get(
            "LITECODE_CONFIG_DIR",
            os.path.join(os.path.expanduser("~"), ".config", "lite"),
        )
    for name in ("opencode.json", "opencode.jsonc"):
        paths.append(os.path.join(global_dir, name))
    opencode_dir = os.environ.get("LITECODE_OPENCODE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "opencode"
    )
    for name in ("opencode.json", "opencode.jsonc"):
        paths.append(os.path.join(opencode_dir, name))
    if project_dir is None:
        project_dir = os.getcwd()
    for name in ("lite.json", "lite.jsonc", "opencode.json", "opencode.jsonc"):
        paths.append(os.path.join(project_dir, name))
    return paths


def load_config(project_dir=None, global_dir=None):
    """Merged OpenCode-style config: global files, then project files."""
    cfg = {}
    for path in _config_paths(project_dir, global_dir):
        if not os.path.isfile(path):
            continue
        try:
            data = load_jsonc(path)
        except (OSError, ValueError) as exc:
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            cfg = _deep_merge(cfg, data)
    return _substitute_env(cfg)


def _split_model_ref(ref):
    """Split 'provider/model': provider may be None; model may contain '/'."""
    if isinstance(ref, dict):
        model = ref.get("model") or ref.get("modelID") or None
        provider = ref.get("providerID") or ref.get("provider") or None
        return provider, model
    if not ref:
        return None, None
    if "/" in ref:
        provider, model = ref.split("/", 1)
        return (provider or None), (model or None)
    return None, ref


def provider_env_key(provider):
    """Conventional env var for a provider's key, e.g. OPENAI_API_KEY."""
    cleaned = provider.upper().replace("-", "_").replace(".", "_").replace(" ", "_")
    return f"{cleaned}_API_KEY"


def _default_model_for_type(ptype):
    if ptype == "codex":
        return CODEX_DEFAULT_MODEL
    if ptype == "agy":
        return AGY_DEFAULT_MODEL
    return DEFAULT_MODEL


def _codex_auth_from_dotjson():
    """Read $CODEX_HOME/auth.json (default ~/.codex/auth.json).

    Returns ``(access_token, account_id)``; matches Codex CLI's
    ``AuthDotJson``/``TokenData`` format (see openai/codex
    codex-rs/login/src/auth/storage.rs, token_data.rs).
    """
    home = os.environ.get("CODEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".codex"
    )
    path = os.path.join(home, "auth.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, None
    tokens = data.get("tokens") or {}
    return tokens.get("access_token"), tokens.get("account_id")


def resolve_config(cfg, model=None, provider=None):
    """Effective provider/model/endpoint/key from flags, env and config."""
    if model is not None:
        cfg_model = model
    else:
        cfg_model = os.environ.get("LITECODE_MODEL") or cfg.get("model")
    if isinstance(cfg_model, dict) and not cfg_model:
        cfg_model = None
    ref_provider, ref_model = _split_model_ref(cfg_model)

    cfg_provider = provider or ref_provider
    if cfg_provider is None:
        cfg_provider = (
            os.environ.get("LITECODE_PROVIDER")
            or cfg.get("provider")
            or DEFAULT_PROVIDER
        )

    entry = (cfg.get("providers") or {}).get(cfg_provider) or {}
    settings = entry.get("settings") or {}
    ptype = entry.get("type") or BUILTIN_PROVIDERS.get(
        cfg_provider, {}
    ).get("type", "openai")
    model_id = ref_model or _default_model_for_type(ptype)

    base_url = settings.get("baseURL") or os.environ.get("LITECODE_API_BASE")
    if base_url is None:
        base_url = (BUILTIN_PROVIDERS.get(cfg_provider) or {}).get("base_url")

    account_id = None
    if ptype == "codex":
        dot_token, dot_account = _codex_auth_from_dotjson()
        env_token = (
            os.environ.get("CODEX_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("CODEX_ACCESS_TOKEN")
            or os.environ.get("LITECODE_API_KEY")
        )
        api_key = settings.get("apiKey") or env_token or dot_token
        account_id = dot_account if api_key == dot_token else None
    elif ptype == "agy":
        api_key = (
            settings.get("apiKey")
            or os.environ.get(provider_env_key(cfg_provider))
            or os.environ.get("LITECODE_API_KEY")
        )
    else:
        api_key = settings.get("apiKey")
        for var in entry.get("env") or []:
            api_key = api_key or os.environ.get(var)
        api_key = (
            api_key
            or os.environ.get(provider_env_key(cfg_provider))
            or os.environ.get("LITECODE_API_KEY")
        )

    return {
        "raw": cfg,
        "provider": cfg_provider,
        "provider_name": entry.get("name") or cfg_provider,
        "model": model_id,
        "base_url": base_url,
        "api_key": api_key,
        "type": ptype,
        "account_id": account_id,
        "models": entry.get("models") or {},
    }
