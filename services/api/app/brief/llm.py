"""大模型撰写事故简报（可选）：配置了 Anthropic 凭据时可用，只把提取的事实交给模型。"""

from __future__ import annotations

import json
import logging
import os

import anthropic

log = logging.getLogger(__name__)

SYSTEM = """你是变电站运行值班负责人，根据 SCADA 系统提取的事故事实，为调度撰写事故简报。

要求：
- 只使用给出的事实，不补充事实中没有的数值、设备或原因；无法确定的写"待现场核实"。
- 纯文本，不使用 Markdown；按以下五节组织：一、事故概况；二、保护及自动装置动作情况；三、事故影响；四、原因初步分析；五、处置建议。
- 动作情况按设备时标顺序概括，写出关键时间间隔（如保护动作到断路器分闸、重合闸时间），并说明是否符合整定配合。
- "classification" 是规则判断的初步定性，可以引用，但要结合动作序列说明理由。
- "later_operations" 是事故过程结束后的人工或遥控操作，不属于事故过程。
- 全文 600 字以内，语言简洁、专业。"""


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class BriefError(Exception):
    pass


async def write(facts: dict, model: str) -> tuple[str, str]:
    """返回 (简报正文, 实际作答的模型)。"""
    client = anthropic.AsyncAnthropic(timeout=120.0)
    try:
        resp = await client.beta.messages.create(
            model=model,
            max_tokens=16000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=1)}],
        )
    except anthropic.AuthenticationError as e:
        raise BriefError("Anthropic 凭据无效") from e
    except anthropic.RateLimitError as e:
        raise BriefError("大模型调用频率超限，请稍后再试") from e
    except anthropic.APIStatusError as e:
        log.warning("brief llm error: %s", e)
        raise BriefError(f"大模型服务返回错误 {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        raise BriefError("无法连接大模型服务") from e
    finally:
        await client.close()
    if resp.stop_reason == "refusal":
        raise BriefError("大模型拒绝了本次请求")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise BriefError("大模型未返回内容")
    return text, resp.model
