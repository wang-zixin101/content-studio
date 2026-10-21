"""OpenAI 兼容协议的提供商实现。

默认用于 MiMo（小米），但任何 OpenAI 兼容服务均可直接使用：
只需在 .env 中改 AI_BASE_URL / AI_API_KEY / 模型名。
"""

from __future__ import annotations

import httpx

from .base import AIError, AIProvider


class OpenAICompatProvider(AIProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, timeout: int = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        text, _meta = await self.chat_with_meta(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        return text

    async def chat_with_meta(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> tuple[str, dict]:
        """真实调用，并把 finish_reason / usage 一并带出。

        为什么要带 finish_reason：mimo-v2.6-pro 是**推理模型**，
        输出上限包含 reasoning_tokens。撞到上限时 HTTP 仍是 200、
        内容也仍是字符串，但 JSON 是半截的 —— 只看状态码会把截断当成成功。

        完成 Token：**max_tokens 为 None（默认）时不写该字段**，
        请求交给供应商默认值（mimo-v2.6-pro 为 131072）。
        本项目四条调用链都不传，因此没有任何人为上限。
        """
        if not self.api_key:
            raise AIError(
                "未配置 AI_API_KEY，请在 backend/.env 中填入密钥", retryable=False
            )

        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            # 仅当调用方显式要求时才发送。mimo-v2.6-pro 默认 thinking=enabled，
            # 官方文档明确：推理模式下自定义 temperature/top_p 会被强制覆写为
            # 1.0/0.95 —— 传了也不生效，因此四条业务链一律不传。
            # 保留这个开关是为了兼容非推理模型 / 将来显式关闭 thinking 的场景。
            payload["temperature"] = temperature
        if max_tokens:
            # 仅在调用方**显式**要求封顶时才发送。
            # 正常业务路径一律不传 —— 见 AIProvider 的「完成 Token 纪律」。
            # MiMo 官方 Chat Completions 使用 max_completion_tokens；
            # 不同时发送 max_tokens，避免依赖旧别名的兼容性。
            payload["max_completion_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._post(client, url, headers, payload)

            # 部分服务不支持 response_format：降级重试
            if resp.status_code == 400 and json_mode:
                body = resp.text.lower()
                if "response_format" in body or "json_object" in body:
                    payload.pop("response_format", None)
                    resp = await self._post(client, url, headers, payload)

        if resp.status_code >= 400:
            # 429 限流与 5xx 服务端错误可重试；其余 4xx（鉴权、参数错误）不可恢复
            raise AIError(
                f"AI 请求失败 [{resp.status_code}] {model}: {resp.text[:300]}",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                # 服务端返回结构异常，通常是偶发，允许重试
                raise AIError(
                    f"AI 返回无 choices：{str(data)[:300]}", retryable=True
                )
            choice = choices[0]
            message = choice.get("message") or {}
            content = message.get("content")
            if not content:
                # 思考模型可能只在 reasoning_content 中给出内容
                content = message.get("reasoning_content")
            if not content:
                raise AIError(
                    f"AI 返回内容为空：{str(data)[:300]}", retryable=True
                )
            meta = {
                "finish_reason": choice.get("finish_reason"),
                "usage": data.get("usage") or {},
            }
            return content, meta
        except AIError:
            raise
        except ValueError as err:  # resp.json() 失败
            raise AIError(
                f"AI 返回非 JSON 响应：{resp.text[:300]}", retryable=True
            ) from err

    @staticmethod
    async def _post(
        client: httpx.AsyncClient,
        url: str,
        headers: dict,
        payload: dict,
    ) -> httpx.Response:
        try:
            return await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as err:
            raise AIError(f"AI 请求超时（{client.timeout}）", retryable=True) from err
        except httpx.HTTPError as err:
            raise AIError(f"AI 网络错误：{err}", retryable=True) from err

    # ---------------------------------------------------------------- Batch
    #
    # 按 OpenAI 兼容协议实现（/files + /batches）。
    # 注意：本实现未经过真实 MiMo 调用验证，仅保证接口形态与调用逻辑正确。

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def upload_batch_file(self, filename: str, content: bytes) -> str:
        if not self.api_key:
            raise AIError("未配置 AI_API_KEY", retryable=False)

        url = f"{self.base_url}/files"
        files = {"file": (filename, content, "application/jsonl")}
        data = {"purpose": "batch"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    url, headers=self._auth_headers(), files=files, data=data
                )
            except httpx.HTTPError as err:
                raise AIError(f"Batch 文件上传失败：{err}", retryable=True) from err

        if resp.status_code >= 400:
            raise AIError(
                f"Batch 文件上传失败 [{resp.status_code}]：{resp.text[:300]}",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
                status_code=resp.status_code,
            )
        file_id = (resp.json() or {}).get("id")
        if not file_id:
            raise AIError(f"Batch 上传未返回文件 id：{resp.text[:200]}")
        return str(file_id)

    async def create_batch(self, input_file_id: str, window: str = "24h") -> dict:
        url = f"{self.base_url}/batches"
        payload = {
            "input_file_id": input_file_id,
            "endpoint": "/v1/chat/completions",
            "completion_window": window,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    url, headers={**self._auth_headers(), "Content-Type": "application/json"},
                    json=payload,
                )
            except httpx.HTTPError as err:
                raise AIError(f"Batch 创建失败：{err}", retryable=True) from err

        if resp.status_code >= 400:
            raise AIError(
                f"Batch 创建失败 [{resp.status_code}]：{resp.text[:300]}",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
                status_code=resp.status_code,
            )
        return resp.json() or {}

    async def get_batch(self, batch_id: str) -> dict:
        url = f"{self.base_url}/batches/{batch_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(url, headers=self._auth_headers())
            except httpx.HTTPError as err:
                raise AIError(f"Batch 查询失败：{err}", retryable=True) from err

        if resp.status_code >= 400:
            raise AIError(
                f"Batch 查询失败 [{resp.status_code}]：{resp.text[:300]}",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
                status_code=resp.status_code,
            )
        return resp.json() or {}

    async def cancel_batch(self, batch_id: str) -> dict:
        """请求取消一个 Batch。MiMo/OpenAI 的取消是**异步**的：
        调用成功只代表请求已受理，随后仍需轮询状态直到 cancelled。
        """
        url = f"{self.base_url}/batches/{batch_id}/cancel"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, headers=self._auth_headers())
            except httpx.HTTPError as err:
                raise AIError(f"Batch 取消失败：{err}", retryable=True) from err

        if resp.status_code >= 400:
            raise AIError(
                f"Batch 取消失败 [{resp.status_code}]：{resp.text[:300]}",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
                status_code=resp.status_code,
            )
        return resp.json() or {}

    async def download_file(self, file_id: str) -> str:
        url = f"{self.base_url}/files/{file_id}/content"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(url, headers=self._auth_headers())
            except httpx.HTTPError as err:
                raise AIError(f"文件下载失败：{err}", retryable=True) from err

        if resp.status_code >= 400:
            raise AIError(
                f"文件下载失败 [{resp.status_code}]：{resp.text[:300]}",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
                status_code=resp.status_code,
            )
        return resp.text
