"""AIProvider 抽象接口 + JSON 解析工具。

任何厂商实现只需继承 AIProvider 并实现 chat()。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any


class AIError(RuntimeError):
    """AI 调用失败（网络、鉴权、限流、返回异常等）。

    retryable=True 表示该错误可通过重试恢复（超时、429、5xx 等）。
    后台队列只在 retryable 时做有限重试，其余直接判定为不可恢复失败。
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str | None) -> dict[str, Any]:
    """从模型输出中稳健地提取 JSON 对象。

    兼容三种常见形态：
    1. 纯 JSON
    2. ```json ... ``` 代码块
    3. 前后夹杂说明文字
    """
    if not text:
        raise AIError("模型返回为空")

    text = text.strip()

    # 1) 直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 2) 代码块
    m = _FENCE_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 3) 截取首个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            data = json.loads(snippet)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise AIError(f"无法从模型输出中解析 JSON：{text[:200]}")


class AIProvider(ABC):
    """AI 提供商抽象。所有实现必须是异步的。

    ------------------------------------------------------------------
    完成 Token 纪律：**默认不设上限**
    ------------------------------------------------------------------
    `max_tokens` 是本层的**透传参数**，默认 None。为 None 时
    openai_compat 不会把 `max_completion_tokens` 写进请求体，
    由供应商使用它自己的默认值（mimo-v2.6-pro 为 131072，
    即该模型的最大输出上限）。

    调用方**不应该主动传值**：
      - 我们无法预知推理模型需要多少 token（reasoning 逐次浮动）；
      - 写死任何数字都只会小于或等于供应商默认值 —— 那是在人为截断自己。

    截断仍然被严密监控：`chat_with_meta` 返回 finish_reason，上层据此判定。
    保留这个参数只是为了「测试可注入」与「极端情况下显式封顶」，
    不是给业务代码用来调优的旋钮。
    """

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """发起一次对话补全，返回助手文本内容。"""
        raise NotImplementedError

    async def chat_with_meta(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """同 chat()，但额外返回调用元信息。

        meta 至少可能包含：
            finish_reason   'stop' / 'length' / ...（无法获取时为 None）
            usage           provider 返回的 token 用量原样透传（可能为空 dict）

        默认实现退化为 chat()，meta 里 finish_reason 为 None ——
        这样测试用的假 provider 不必实现它也能工作。
        真实 provider 应当覆盖本方法，否则**无法判断推理模型是否被截断**：
        max_completion_tokens 包含 reasoning_tokens，撞上限时 JSON 会半截。
        """
        text = await self.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        return text, {"finish_reason": None, "usage": {}}

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 1,
    ) -> dict[str, Any]:
        """发起对话并解析为 JSON 对象；解析失败时重试。"""
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            text = await self.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
            try:
                return extract_json(text)
            except AIError as err:
                last_err = err
                # 追加纠正提示后重试
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": "只输出合法的 JSON 对象本身，不要包含任何解释文字或 Markdown 代码块。",
                    },
                ]
        raise last_err or AIError("JSON 解析失败")

    # ---------------- Batch（可选能力，默认不支持）----------------

    async def upload_batch_file(self, filename: str, content: bytes) -> str:
        raise NotImplementedError("当前 AI 提供商不支持 Batch 文件上传")

    async def create_batch(self, input_file_id: str, window: str = "24h") -> dict[str, Any]:
        raise NotImplementedError("当前 AI 提供商不支持 Batch")

    async def get_batch(self, batch_id: str) -> dict[str, Any]:
        raise NotImplementedError("当前 AI 提供商不支持 Batch")

    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        raise NotImplementedError("当前 AI 提供商不支持取消 Batch")

    async def download_file(self, file_id: str) -> str:
        raise NotImplementedError("当前 AI 提供商不支持文件下载")
