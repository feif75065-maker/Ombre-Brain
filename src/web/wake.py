"""
web/wake.py — 海马体唤醒接口 /api/wake

返回人格层（pinned 完整内容）+ 近期层（动态记忆摘要），
供拾光后端每次新对话时注入 system prompt。

对外暴露：register(mcp)。
"""

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import _shared as sh

logger = logging.getLogger("ombre_brain")

_TOKEN_BUDGET = 6000


def register(mcp) -> None:

    @mcp.custom_route("/api/wake", methods=["GET"])
    async def wake_endpoint(request: Request) -> JSONResponse:
        try:
            from utils import strip_wikilinks, count_tokens_approx
        except ImportError:
            from ..utils import strip_wikilinks, count_tokens_approx

        try:
            all_buckets = await sh.bucket_mgr.list_all(include_archive=False)

            # --- 人格层：pinned 记忆完整内容 ---
            pinned = [
                b for b in all_buckets
                if b["metadata"].get("pinned") or b["metadata"].get("protected")
            ]
            identity_parts = []
            for b in pinned:
                name = b["metadata"].get("name", b["id"])
                content = strip_wikilinks(b["content"]).strip()
                identity_parts.append(f"[{name}]\n{content}")

            # --- 近期层：权重最高的动态记忆 ---
            dynamic = [
                b for b in all_buckets
                if not b["metadata"].get("resolved", False)
                and b["metadata"].get("type") not in ("permanent", "feel")
                and not b["metadata"].get("pinned")
                and not b["metadata"].get("protected")
            ]
            scored = sorted(
                dynamic,
                key=lambda b: sh.decay_engine.calculate_score(b["metadata"]),
                reverse=True,
            )[:10]

            recent_parts = []
            token_budget = _TOKEN_BUDGET
            for b in scored:
                if token_budget <= 0:
                    break
                name = b["metadata"].get("name", b["id"])
                content = strip_wikilinks(b["content"]).strip()
                t = count_tokens_approx(content)
                if t > token_budget:
                    break
                recent_parts.append(f"[{name}]\n{content}")
                token_budget -= t

            # --- 组装 prompt 文本 ---
            prompt_parts = []
            if identity_parts:
                prompt_parts.append(
                    "=== 人格层（核心记忆）===\n" + "\n---\n".join(identity_parts)
                )
            if recent_parts:
                prompt_parts.append(
                    "=== 近期记忆 ===\n" + "\n---\n".join(recent_parts)
                )

            prompt_text = "\n\n".join(prompt_parts)

            return JSONResponse({
                "ok": True,
                "identity_count": len(identity_parts),
                "recent_count": len(recent_parts),
                "prompt_text": prompt_text,
            })
        except Exception as e:
            logger.warning(f"Wake endpoint failed: {e}")
            return JSONResponse({"ok": False, "error": str(e), "prompt_text": ""})
