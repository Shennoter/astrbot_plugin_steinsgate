import asyncio
import random
import time

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core import (
    DataStore,
    dqq_timestamp,
    normalize_divergence,
    parse_dqq,
    render_meter,
)


class SteinsGate(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.store = DataStore(StarTools.get_data_dir("astrbot_plugin_steinsgate"))

    async def _meter(self, event, value="", special=False):
        try:
            return await self._build_meter(event, value, special)
        except ValueError as exc:
            return event.plain_result(str(exc))
        except Exception:
            logger.exception("世界线图片生成失败")
            return event.plain_result("生成失败，请检查插件日志和 UniDiv.json 数据。")

    async def _build_meter(self, event, value="", special=False):
        text = ""
        if special:
            symbols, text = await asyncio.to_thread(self.store.special, value)
        elif value:
            symbols = normalize_divergence(value)
        elif random.random() * 100 < max(
            0, min(100, int(self.config.get("unique_probability", 50)))
        ):
            symbols, text = await asyncio.to_thread(self.store.special)
        else:
            symbols = random.choice("-01234") + "".join(
                random.choices("0123456789", k=6)
            )
        image = await asyncio.to_thread(render_meter, symbols)
        chain = [Comp.Image.fromBytes(image)]
        if text:
            chain.append(Comp.Plain("\n" + text))
        return event.chain_result(chain)

    @filter.command("div", alias={"世界线"})
    async def div(self, event: AstrMessageEvent, divergence: str = ""):
        """生成世界线变动率图片：/div [1.048596]"""
        event.stop_event()
        yield await self._meter(event, divergence)

    @filter.command("udiv")
    async def udiv(self, event: AstrMessageEvent):
        """随机抽取特殊世界线及介绍。"""
        event.stop_event()
        yield await self._meter(event, special=True)

    @filter.command("timeline", alias={"年表"})
    async def timeline(self, event: AstrMessageEvent, year: str = ""):
        """查询某年份全部事件，不填年份则随机抽取。"""
        event.stop_event()
        try:
            messages = await asyncio.to_thread(self.store.timeline, year)
            # Bundle records to avoid flooding a group with dozens of messages.
            combined = "\n\n".join(messages)
            for start in range(0, len(combined), 2800):
                yield event.plain_result(combined[start : start + 2800])
        except Exception:
            logger.exception("年表读取失败")
            yield event.plain_result(
                "年表读取失败，请检查插件数据目录中的 Timeline.json 和日志。"
            )

    async def _dqq(self, event, arguments):
        try:
            content, offset = parse_dqq(
                arguments, int(self.config.get("dqq_default_seconds", 432000))
            )
            result, _, _ = await self._send_dqq(event, content, offset)
            return result
        except ValueError as exc:
            return event.plain_result(str(exc))

    async def _send_dqq(self, event, content, offset, target_datetime=""):
        if not isinstance(content, str) or not content.strip():
            raise ValueError("请输入 D-QQ 正文")
        if not target_datetime and (
            isinstance(offset, bool)
            or not isinstance(offset, (int, float))
            or not float(offset).is_integer()
        ):
            raise ValueError("时间偏移必须为整数秒")
        target, date = dqq_timestamp(
            0 if target_datetime else int(offset), int(time.time()), target_datetime
        )
        if event.get_platform_name() == "aiocqhttp":
            node = {
                "type": "node",
                "data": {
                    "uin": str(event.get_sender_id()),
                    "name": event.get_sender_name(),
                    "user_id": str(event.get_sender_id()),
                    "nickname": event.get_sender_name(),
                    "time": target,
                    "content": [{"type": "text", "data": {"text": content}}],
                },
            }
            try:
                group = event.get_group_id()
                if group:
                    await event.bot.call_action(
                        "send_group_forward_msg", group_id=int(group), messages=[node]
                    )
                else:
                    await event.bot.call_action(
                        "send_private_forward_msg",
                        user_id=int(event.get_sender_id()),
                        messages=[node],
                    )
                return None, "合并转发", date
            except Exception:
                logger.warning("D-QQ 合并转发发送失败，改用文本发送", exc_info=True)
        return (
            event.plain_result(content),
            "普通文本",
            date,
        )

    @filter.llm_tool(name="steinsgate_divergence")
    async def tool_divergence(
        self, event: AstrMessageEvent, divergence: str = ""
    ) -> str:
        """生成并直接发送命运石之门辉光管世界线变动率图片。
        用户想看变动率、生成世界线图片时使用。无数值时随机生成，可能附带特殊世界线介绍。
        已知用户想看特殊世界线的剧情资料时，优先使用 steinsgate_special_worldline。
        图片由工具直接发送，不要用 Markdown 伪造图片链接或重复调用。

        Args:
            divergence(string): 用户指定的变动率，如 1.048596、0.571024；未指定时传空字符串。保留前导零，不要自行杜撰用户指定的数值。
        """
        try:
            result = await self._build_meter(event, divergence)
            await event.send(result)
            return "世界线图片已直接发送到当前会话。无需再次发送或生成图片链接。"
        except ValueError as exc:
            return f"未发送：{exc}"
        except Exception:
            logger.exception("LLM 世界线工具失败")
            return "世界线图片生成或发送失败，请检查插件日志；不要声称已发送。"

    @filter.llm_tool(name="steinsgate_special_worldline")
    async def tool_special(self, event: AstrMessageEvent, divergence: str = "") -> str:
        """查询并直接发送特殊世界线的辉光管图片与内置剧情资料（包含剧透）。
        用户想抽取特殊世界线、了解某条世界线或查看石头门线时使用。
        石头门线的变动率为 1.048596。空字符串表示随机抽取；查询不到时告知用户，不编造资料。

        Args:
            divergence(string): 指定世界线变动率，如 1.048596；随机抽取时传空字符串。
        """
        try:
            result = await self._build_meter(event, divergence, special=True)
            await event.send(result)
            return "特殊世界线图片和内置剧情介绍已直接发送到当前会话，无需重复发送。"
        except ValueError as exc:
            return f"未发送：{exc}"
        except Exception:
            logger.exception("LLM 特殊世界线工具失败")
            return "特殊世界线查询或发送失败，请检查插件日志；不要声称已发送。"

    @filter.llm_tool(name="steinsgate_timeline")
    async def tool_timeline(self, event: AstrMessageEvent, year: str = "") -> str:
        """查询并直接发送命运石之门年表事件（包含剧透）。
        用户询问该作品某一年发生的事件、查看年表或随机了解历史事件时使用。
        指定年份会返回该年全部内置事件，未指定则随机抽取一条。这不是现实世界历史查询工具。

        Args:
            year(string): 四位年份，如 2010；未指定年份、随机事件时传空字符串。
        """
        try:
            messages = await asyncio.to_thread(self.store.timeline, year)
            if messages == ["无此年份"]:
                return f"内置命运石之门年表没有 {year} 年的记录，未发送消息。不要编造查询结果。"
            combined = "\n\n".join(messages)
            for start in range(0, len(combined), 2800):
                await event.send(event.plain_result(combined[start : start + 2800]))
            return f"已直接发送 {len(messages)} 条年表事件到当前会话，无需重复发送。"
        except Exception:
            logger.exception("LLM 年表工具失败")
            return "年表查询或发送未完成，请检查插件数据和日志；不要声称全部发送成功。"

    @filter.llm_tool(name="steinsgate_send_dqq")
    async def tool_dqq(
        self,
        event: AstrMessageEvent,
        content: str,
        offset_seconds: int = 432000,
        target_datetime: str = "",
    ) -> str:
        """在当前会话立即发送一条带过去或未来时间的 D-QQ，模拟石头门的时空短信。
        用户要求发 D-QQ、给过去/未来的自己留言、把一句话显示成过去/未来发来的消息时使用。
        这是即时发送的娱乐功能，不是定时发送、提醒、真实时间旅行，也不能给其他会话或联系人发消息。
        用户要求五天后再发送/到时候提醒时不要调用；若正文或时间意图不清先澄清。
        使用请求者本人的昵称和 QQ 号，不冒充其他人。一次请求只调用一次；工具成功后不要再发送相同正文。
        用户指定了年月日时，必须通过 target_datetime 原样传递日期，禁止自行计算与现在相差的秒数。

        Args:
            content(string): 要发送的消息正文，保留用户指定文字、空格、引号和数字；用户未指定正文时先询问。
            offset_seconds(number): 仅用于相对时间的整数秒数，过去为负、未来为正。如五天前=-432000，明天=86400。未指定时间时为432000。设置 target_datetime 时此参数被忽略，可传0。
            target_datetime(string): 用户指定的绝对日期或时刻，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。例如2014年12月25日传2014-12-25，由代码计算时间戳，禁止换算为相对秒数。没有时刻时为当天00:00:00，未提供时区时使用北京时间UTC+8；支持ISO时区后缀。用户只指定相对时间时传空字符串。
        """
        try:
            result, mode, date = await self._send_dqq(
                event, content, offset_seconds, target_datetime
            )
            logger.info(
                "D-QQ resolved target=%s; absolute=%s", date, bool(target_datetime)
            )
            if result is not None:
                await event.send(result)
            return (
                f"D-QQ 已立即发送到当前会话，形式：{mode}。未创建定时任务。"
                "用户只需要原消息，不要重复正文，也不要补发时间戳或额外说明。"
            )
        except ValueError as exc:
            return f"未发送：{exc}"
        except Exception:
            logger.exception("LLM D-QQ 工具失败")
            return "D-QQ 发送失败，请检查插件日志；不要声称已发送或创建定时任务。"

    @filter.command("dqq", alias={"D-QQ", "发送"})
    async def dqq(self, event: AstrMessageEvent):
        """发送 D-QQ：/dqq 内容 [相对当前时间的秒数]"""
        event.stop_event()
        # Read the remaining raw text to preserve spaces and quoted content.
        parts = event.message_str.strip().split(maxsplit=1)
        result = await self._dqq(event, parts[1] if len(parts) == 2 else "")
        if result is not None:
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def bare_dqq(self, event: AstrMessageEvent):
        """Compatibility with Mirai's bare D-QQ keywords, including custom ones."""
        if not self.config.get("enable_bare_dqq", True):
            return
        parts = event.message_str.strip().split(maxsplit=1)
        commands = self.config.get("dqq_commands", ["dqq", "发送", "D-QQ"])
        if not parts or parts[0] not in commands:
            return
        # Standard command handlers own awakened messages, avoiding duplicate sends.
        if event.is_at_or_wake_command and parts[0] in {"dqq", "D-QQ", "发送"}:
            return
        event.stop_event()
        result = await self._dqq(event, parts[1] if len(parts) == 2 else "")
        if result is not None:
            yield result

    @filter.command("sghelp")
    async def help(self, event: AstrMessageEvent):
        event.stop_event()
        yield event.plain_result(
            "命运石之门\n/div [变动率]（别名：世界线）\n/udiv 特殊世界线\n"
            "/timeline [年份]（别名：年表）\n/dqq 内容 [秒数]（别名：D-QQ、发送）\n"
            '例：dqq "这是来自过去的消息" -432000\n'
            "默认偏移 +432000 秒（未来五天），即时发送，非定时任务。"
            "\n也可直接自然语言请求上述功能，由支持工具调用的 LLM 自动选择工具。"
        )
