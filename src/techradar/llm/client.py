"""LLM 客户端：OpenAI 兼容 /chat/completions（OpenCode Zen/Go、DeepSeek 等已验证）。

- 配置优先级：环境变量 > 参数 > config/llm.yaml
- 429/5xx 与网络错误自动指数退避重试；4xx 直接抛 LLMError
- 输出要求严格 JSON，并提供代码块剥离 / 局部提取的解析兜底
- 发送稳定 x-opencode-session 头（OpenCode Go 路由与缓存建议）
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import requests

from techradar.utils.logging import get_logger
from techradar.utils.retry import retry

_RETRYABLE_STATUS = (429, 500, 502, 503, 504)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def _stable_session_id() -> str:
    """稳定的 OpenCode 会话 id。

    OpenCode Go 要求同一客户端复用同一 x-opencode-session（缺失或不稳定会导致
    HTTP 400 MissingSessionID）。这里生成一次并持久化到数据目录，跨进程复用。
    """
    import hashlib
    import json
    from pathlib import Path

    cache = Path(os.environ.get("TECHRADAR_DATA_DIR", "data")) / "llm_session.json"
    if cache.is_file():
        try:
            stored = json.loads(cache.read_text(encoding="utf-8")).get("session_id")
            if stored:
                return str(stored)
        except (OSError, ValueError):
            pass
    seed = f"{os.environ.get('LLM_BASE_URL', '')}|{os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'host')}"
    session_id = "techradar-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
    except OSError:
        pass
    return session_id


class LLMError(RuntimeError):
    """LLM 调用错误（不可重试，如参数错误 / 鉴权失败 / JSON 解析失败）。"""


class LLMRetryableError(LLMError):
    """LLM 临时错误（429/5xx，可重试）。"""


class LLMResponseError(LLMError):
    """响应结构、输出 JSON 或 JSON 模式兼容错误。"""


def parse_json_object(content: str) -> dict[str, Any]:
    """从 LLM 输出中解析 JSON：先整体解析，再剥离代码块，最后截取 {…}。"""
    text = (content or "").strip()
    fence = _JSON_FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise LLMResponseError("LLM 输出不是合法 JSON 对象") from None
        else:
            raise LLMResponseError("LLM 输出不是合法 JSON 对象") from None
    if not isinstance(parsed, dict):
        raise LLMResponseError("LLM 输出必须是 JSON 对象，不能是数组或标量")
    return parsed


class LLMClient:
    """统一 LLM 调用客户端（结构化 JSON 输出）。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        session_id: str | None = None,
        config=None,
    ) -> None:
        llm_cfg = config.llm if config is not None else {}
        self.logger = get_logger("techradar.llm.client")
        self.api_key = api_key or os.environ.get("LLM_API_KEY") or ""
        self.base_url = (
            base_url or os.environ.get("LLM_BASE_URL") or str(llm_cfg.get("base_url", ""))
        ).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or str(
            llm_cfg.get("model", "deepseek-v4-flash")
        )
        self.temperature = float(
            temperature if temperature is not None else llm_cfg.get("temperature", 0.2)
        )
        self.max_tokens = int(max_tokens or llm_cfg.get("max_tokens", 2000))
        self.timeout = float(
            timeout if timeout is not None else llm_cfg.get("timeout_seconds", 90)
        )
        self.session_id = session_id or os.environ.get("TECHRADAR_SESSION_ID") or _stable_session_id()
        if not self.api_key:
            raise LLMError("未配置 LLM_API_KEY（环境变量或 .env）")
        if not self.base_url:
            raise LLMError(
                "未配置 LLM_BASE_URL（如 OpenCode Go: https://opencode.ai/zen/go/v1）"
            )

    def chat_json(
        self,
        system: str,
        user: str,
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """调用 chat/completions，返回解析后的 JSON dict（附带 _usage）。

        json_mode=True 时会请求 response_format=json_object；但部分推理模型会把
        token 预算全用在 reasoning 上导致 content 为空（finish_reason=length）。
        此时自动降级为普通模式重试一次，避免整条分析失败。
        """
        limit = self.max_tokens if max_tokens is None else max_tokens
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": limit,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            return self._parse_response(self._post(payload))
        except LLMResponseError:
            if not json_mode:
                raise
            # json_mode 被推理 token 吃满 / 输出格式不兼容 -> 降级普通模式（放宽 token 上限）
            self.logger.warning("json_mode 响应不可用，降级为普通模式重试")
            fallback = dict(payload)
            fallback.pop("response_format", None)
            # 推理模型会把大量 token 花在 reasoning 上，需显著放宽上限
            fallback["max_tokens"] = max(int(limit * 3), 6000)
            try:
                return self._parse_response(self._post(fallback))
            except requests.RequestException as exc:
                raise LLMError(f"LLM 网络请求重试后失败（{type(exc).__name__}）") from exc
            except LLMResponseError as exc:
                if "截断" not in str(exc):
                    raise
                # 仍被截断：再放宽一次
                self.logger.warning("普通模式仍被截断，进一步放宽 token 上限重试")
                retry_payload = dict(fallback)
                retry_payload["max_tokens"] = max(int(limit * 5), 12000)
                try:
                    return self._parse_response(self._post(retry_payload))
                except requests.RequestException as net_exc:
                    raise LLMError(
                        f"LLM 网络请求重试后失败（{type(net_exc).__name__}）"
                    ) from net_exc

    def _parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """从响应中取出 content 并解析 JSON。"""
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("LLM 响应缺少有效 message.content") from exc
        if not isinstance(content, str) or not content.strip():
            reason = choice.get("finish_reason")
            raise LLMResponseError(
                f"LLM 响应为空（finish_reason={reason}，可能被推理 token 占满）"
            )
        if choice.get("finish_reason") == "length":
            raise LLMResponseError("LLM 输出被 token 上限截断")
        parsed = parse_json_object(content)
        usage = data.get("usage", {})
        parsed["_usage"] = usage if isinstance(usage, dict) else {}
        return parsed

    @retry(
        max_attempts=3,
        base_delay=1.0,
        max_delay=8.0,
        exceptions=(LLMRetryableError, requests.RequestException),
    )
    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "simulation-tech-radar/0.1",
            "x-opencode-session": self.session_id,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            # 退避装饰器会记录异常，先去除可能携带凭据的 URL 与响应文本。
            raise LLMRetryableError(f"LLM 网络请求失败（{type(exc).__name__}）") from exc
        if resp.status_code in _RETRYABLE_STATUS:
            raise LLMRetryableError(f"LLM 服务暂时不可用（HTTP {resp.status_code}）")
        if resp.status_code >= 400:
            body = resp.text.lower()
            if payload.get("response_format") and resp.status_code in (400, 422) and any(
                term in body for term in ("response_format", "json mode", "json_mode", "json_object")
            ):
                raise LLMResponseError("LLM 服务不支持当前 JSON 模式")
            raise LLMError(f"LLM 请求失败（HTTP {resp.status_code}）")
        try:
            result = resp.json()
        except ValueError as exc:
            raise LLMResponseError("LLM 服务返回了非 JSON 响应") from exc
        if not isinstance(result, dict):
            raise LLMResponseError("LLM 服务响应必须为 JSON 对象")
        return result
