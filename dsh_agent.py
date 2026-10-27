#!/usr/bin/env python3
"""QQ 官方 botpy ↔ DeepSeek Harness Agent 桥接。

和 ``LocalModel`` 一样提供 ``ask`` / ``chat`` / ``aclose`` 接口，但底层不是
OpenAI 兼容端点，而是官方 Python SDK（``deepseek_harness``）启动的
``--profile sdk`` agent：

    腾讯平台 --WSS--> official_bot.py --JSON-RPC--> dsh sdk runtime --模型--> 本地/云端

每个 QQ 会话（私聊 openid、群号 + 发言人）映射一个持久 dsh Session；人设只在
会话第一轮注入一次，之后交给 dsh 自己的会话历史和压缩机制。

配置项（config.json，均可选）：

- ``agent_backend``：``"dsh"`` 启用本模块，其余值走原来的 ``LocalModel``
- ``dsh_provider`` / ``dsh_model``：provider 路由与模型 id，默认本地 Strata
- ``dsh_home``：SDK 的 Harness home（独立于 ``~/.dsh``），默认 ``.dsh-qq-home``
- ``dsh_cwd``：agent 工作目录，默认 ``agent-workspace``
- ``dsh_patch``：provider 覆盖文件，默认生成 ``dsh-qq.patch.yml``
- ``dsh_timeout``：单轮超时秒数，默认 600（agent 带工具时一轮可能较久）
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

from deepseek_harness import DeepSeekHarness

HERE = Path(__file__).resolve().parent

# sdk profile 默认没有本地 provider；这份 patch 把 llm-pi-ai 指到 Strata。
DEFAULT_PATCH = """\
- id: llm-pi-ai
  config:
    providers:
      local-qwen:
        api: openai-completions
        baseURL: http://127.0.0.1:8080/v1
        apiKeyEnv: LOCAL_LLM_KEY
        compat:
          supportsDeveloperRole: false
          maxTokensField: max_tokens
        models:
          - id: qwen3.8-flash-next-iq2_xs
            contextWindow: 32768
            maxTokens: 8192
"""


class DshAgent:
    """把 dsh agent 包装成 ``LocalModel`` 的接口，供 Responder 直接替换。"""

    def __init__(self, cfg: Any, personas: Any = None) -> None:
        self.cfg = cfg
        self.personas = personas
        self.sticker_lib: Any = None  # Responder 会赋值，保持接口一致
        self._values = getattr(cfg, "values", {}) or {}
        self._lock = asyncio.Lock()
        self._harness: DeepSeekHarness | None = None
        self._sessions: dict[str, str] = {}
        self._greeted: set[str] = set()
        self._waiting = 0

    @property
    def overloaded(self) -> bool:
        return self._waiting >= int(self._values.get("queue_limit", 3) or 3)

    # ---- 配置与生命周期 ----

    def _get(self, key: str, default: Any) -> Any:
        value = self._values.get(key)
        return default if value in (None, "") else value

    def _resolve_patch(self) -> tuple[str, ...]:
        configured = self._get("dsh_patch", "")
        path = Path(str(configured)).expanduser().resolve() if configured else HERE / "dsh-qq.patch.yml"
        if not path.exists() and not configured:
            path.write_text(DEFAULT_PATCH, "utf-8")
        return (str(path),) if path.exists() else ()

    def _harness_for(self) -> DeepSeekHarness:
        if self._harness is None:
            home = Path(str(self._get("dsh_home", HERE / ".dsh-qq-home"))).expanduser().resolve()
            cwd = Path(str(self._get("dsh_cwd", HERE / "agent-workspace"))).expanduser().resolve()
            home.mkdir(parents=True, exist_ok=True)
            cwd.mkdir(parents=True, exist_ok=True)
            env = {"LOCAL_LLM_KEY": os.environ.get("LOCAL_LLM_KEY", "sk-local")}
            self._harness = DeepSeekHarness(
                dsh_home=str(home),
                cwd=str(cwd),
                provider=str(self._get("dsh_provider", "local-qwen")),
                model=str(self._get("dsh_model", "qwen3.8-flash-next-iq2_xs")),
                patches=self._resolve_patch(),
                env=env,
                request_timeout_seconds=float(self._get("dsh_timeout", 600) or 600),
            )
        return self._harness

    def _session_id(self, key: str) -> str:
        session_id = self._sessions.get(key)
        if session_id is None:
            digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
            session_id = f"qq-{digest}"
            self._sessions[key] = session_id
        return session_id

    def _system_prompt(self) -> str:
        if self.personas is not None:
            try:
                return str(self.personas.prompt())
            except Exception:
                pass
        return str(self._values.get("system_prompt", "") or "")

    def _run(self, key: str, prompt: str) -> str:
        harness = self._harness_for()
        session_id = self._session_id(key)
        text = prompt
        if key not in self._greeted:
            system = self._system_prompt().strip()
            if system:
                text = f"【角色设定，必须始终遵循】\n{system}\n\n【用户消息】\n{prompt}"
            self._greeted.add(key)
        # 会话已存在时必须 resume（同名 start_session 会报 already exists）；
        # 不存在时 resume 抛错，再走新建。
        try:
            session = harness.resume_session(session_id)
        except Exception:
            session = harness.start_session(session_id)
        result = session.run(text)
        return result.final_response or ""

    # ---- LocalModel 兼容接口 ----

    async def ask(self, session: str, prompt: str, system_suffix: str = "") -> str:
        """带 dsh 会话历史的串行推理；``system_suffix`` 只影响本轮。"""
        self._waiting += 1
        try:
            async with self._lock:
                text = f"{prompt}\n\n【本轮补充】{system_suffix}" if system_suffix else prompt
                reply = await asyncio.to_thread(self._run, session, text)
                if not reply.strip():
                    raise RuntimeError("dsh agent 返回空正文")
                return reply
        finally:
            self._waiting -= 1

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """兼容旧接口：把完整消息列表压成一条 prompt。"""
        lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages]
        return await self.ask("chat", "\n".join(lines))

    async def aclose(self) -> None:
        if self._harness is not None:
            self._harness.close()
            self._harness = None
