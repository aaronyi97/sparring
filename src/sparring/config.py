"""配置加载：config.yaml + .env 环境变量解析。

约束（must-keep）：base_url / api_key 缺失即 fail-closed 抛 ConfigError，
不静默回退官方端点；每个模型有独立 timeout 与价格口径（成本入账依赖）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_name: str
    api_key: str
    base_url: str
    timeout_seconds: float
    max_tokens: int
    cost_per_1m_input: float
    cost_per_1m_output: float
    temperature: float | None = None
    reasoning_effort: str | None = None
    proxy_url: str | None = None


@dataclass(frozen=True)
class AppConfig:
    raw: dict
    root: Path

    @property
    def db_path(self) -> Path:
        p = Path(self.raw["app"]["db_path"])
        return p if p.is_absolute() else self.root / p

    def role_model_keys(self, role: str) -> list[str]:
        v = self.raw["roles"][role]
        return list(v) if isinstance(v, list) else [v]

    def fanout_params(self) -> dict:
        f = self.raw.get("fanout", {})
        return {
            "n_of_m": int(f.get("n_of_m", 2)),
            "per_model_timeout": float(f.get("per_model_timeout_seconds", 90)),
            "total_timeout": float(f.get("total_timeout_seconds", 120)),
        }

    def resolve(self, model_key: str) -> ModelSpec:
        try:
            m = self.raw["models"][model_key]
        except KeyError as e:
            raise ConfigError(f"config.yaml 缺少模型定义: {model_key}") from e
        api_key = os.environ.get(m["api_key_env"], "").strip()
        base_url = os.environ.get(m["base_url_env"], "").strip()
        if not api_key:
            raise ConfigError(f"{model_key}: 环境变量 {m['api_key_env']} 未设置（fail-closed）")
        if not base_url:
            raise ConfigError(f"{model_key}: 环境变量 {m['base_url_env']} 未设置（fail-closed，不回退官方端点）")
        proxy_url = None
        if m.get("proxy_env"):
            proxy_url = os.environ.get(m["proxy_env"], "").strip() or None
        return ModelSpec(
            key=model_key,
            model_name=m["model_name"],
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=float(m.get("timeout_seconds", 60)),
            max_tokens=int(m.get("max_tokens", 4096)),
            cost_per_1m_input=float(m["cost_per_1m_input"]),
            cost_per_1m_output=float(m["cost_per_1m_output"]),
            temperature=m.get("temperature"),
            reasoning_effort=m.get("reasoning_effort"),
            proxy_url=proxy_url,
        )


def _load_env_file(path: Path) -> None:
    """最小 KEY=VALUE 解析；已存在的环境变量不覆盖。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def load_config(root: str | Path | None = None) -> AppConfig:
    root_path = Path(root) if root else Path(__file__).resolve().parents[2]
    cfg_file = root_path / "config.yaml"
    if not cfg_file.exists():
        raise ConfigError(f"找不到 config.yaml: {cfg_file}")
    env_file = root_path / ".env"
    if env_file.exists():
        _load_env_file(env_file)
    raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    return AppConfig(raw=raw, root=root_path)
