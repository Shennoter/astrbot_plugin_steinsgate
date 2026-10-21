"""API contract tests. Stubs deliberately do not claim live AstrBot/QQ coverage."""

import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    def decorator(*args, **kwargs):
        return lambda func: func

    class Star:
        def __init__(self, context):
            self.context = context

    class Plain:
        def __init__(self, text):
            self.text = text

    class Image:
        @staticmethod
        def fromBytes(data):
            return data

    modules = {
        "astrbot": {},
        "astrbot.api": {"AstrBotConfig": dict, "logger": logging.getLogger("test")},
        "astrbot.api.message_components": {"Plain": Plain, "Image": Image},
        "astrbot.api.event": {
            "AstrMessageEvent": object,
            "filter": types.SimpleNamespace(
                command=decorator,
                llm_tool=decorator,
                event_message_type=decorator,
                EventMessageType=types.SimpleNamespace(ALL="all"),
            ),
        },
        "astrbot.api.star": {
            "Context": object,
            "Star": Star,
            "StarTools": types.SimpleNamespace(
                get_data_dir=lambda name: tmp_path,
            ),
        },
    }
    for name, attrs in modules.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
    name = "astrbot_plugin_steinsgate._test_main"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SteinsGate(None, {})


class Event:
    def __init__(self, text="", platform="aiocqhttp", group="123"):
        self.message_str = text
        self.platform = platform
        self.group = group
        self.is_at_or_wake_command = False
        self.bot = types.SimpleNamespace(call_action=AsyncMock())
        self.stopped = False
        self.send = AsyncMock()

    def stop_event(self):
        self.stopped = True

    def plain_result(self, value):
        return value

    def chain_result(self, value):
        return value

    def get_platform_name(self):
        return self.platform

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return "456"

    def get_sender_name(self):
        return "测试者"


async def collect(gen):
    return [result async for result in gen]


@pytest.mark.asyncio
async def test_div_and_invalid_input(plugin):
    event = Event()
    result = await collect(plugin.div(event, "1.048596"))
    assert event.stopped and result[0][0].startswith(b"\x89PNG")
    assert "格式错误" in (await collect(plugin.div(Event(), "abc")))[0]


@pytest.mark.asyncio
async def test_probability_limits_and_udiv(plugin):
    plugin.config["unique_probability"] = 0
    assert len((await collect(plugin.div(Event())))[0]) == 1
    plugin.config["unique_probability"] = 100
    assert len((await collect(plugin.div(Event())))[0]) == 2
    assert len((await collect(plugin.udiv(Event())))[0]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["123", ""])
async def test_onebot_forward_time(plugin, group, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1700000000)
    event = Event('dqq "hello world" -60', group=group)
    assert await collect(plugin.dqq(event)) == []
    call = event.bot.call_action.call_args
    assert call.args[0] == (
        "send_group_forward_msg" if group else "send_private_forward_msg"
    )
    node = call.kwargs["messages"][0]["data"]
    assert node["time"] == 1699999940
    assert node["uin"] == "456" and node["nickname"] == "测试者"
    assert node["content"][0]["data"]["text"] == "hello world"


@pytest.mark.asyncio
async def test_forward_failure_and_other_platform(plugin):
    event = Event("dqq hi")
    event.bot.call_action.side_effect = RuntimeError("unsupported forward")
    result = await collect(plugin.dqq(event))
    assert result == ["hi"]
    event = Event("dqq hi", platform="telegram")
    assert await collect(plugin.dqq(event)) == ["hi"]
    event.bot.call_action.assert_not_called()


@pytest.mark.asyncio
async def test_bare_custom_commands_and_duplicate_guard(plugin):
    event = Event("dqq test", platform="telegram")
    assert len(await collect(plugin.bare_dqq(event))) == 1
    event.is_at_or_wake_command = True
    assert await collect(plugin.bare_dqq(event)) == []
    plugin.config["dqq_commands"] = ["时间跳跃"]
    assert await collect(plugin.bare_dqq(Event("dqq test"))) == []
    custom = Event("时间跳跃 内容", platform="telegram")
    assert len(await collect(plugin.bare_dqq(custom))) == 1
    plugin.config["enable_bare_dqq"] = False
    assert await collect(plugin.bare_dqq(custom)) == []


@pytest.mark.asyncio
async def test_year_chunking_and_bad_data(plugin):
    result = await collect(plugin.timeline(Event(), "2010"))
    assert result and all(len(chunk) <= 2800 for chunk in result)
    (plugin.store.data_dir / "Timeline.json").write_text("broken", encoding="utf-8")
    assert "年表读取失败" in (await collect(plugin.timeline(Event())))[0]


@pytest.mark.asyncio
async def test_dqq_empty(plugin):
    event = Event("dqq")
    assert "请输入内容" in (await collect(plugin.dqq(event)))[0]
    event.bot.call_action.assert_not_called()


@pytest.mark.asyncio
async def test_llm_divergence_and_special(plugin):
    event = Event()
    assert "已直接发送" in await plugin.tool_divergence(event, "1.048596")
    assert event.send.call_args.args[0][0].startswith(b"\x89PNG")
    event.send.reset_mock()
    assert "未发送" in await plugin.tool_divergence(event, "oops")
    event.send.assert_not_called()
    assert "已直接发送" in await plugin.tool_special(event, "1.048596")
    assert len(event.send.call_args.args[0]) == 2
    event.send.reset_mock()
    assert "未发送" in await plugin.tool_special(event, "9.999999")
    event.send.assert_not_called()
    assert not event.stopped


@pytest.mark.asyncio
async def test_llm_timeline_delivery(plugin):
    event = Event()
    assert "已直接发送" in await plugin.tool_timeline(event, "2010")
    combined = "".join(call.args[0] for call in event.send.call_args_list)
    assert combined == "\n\n".join(plugin.store.timeline("2010"))
    event.send.reset_mock()
    assert "没有 9999 年" in await plugin.tool_timeline(event, "9999")
    event.send.assert_not_called()
    assert not event.stopped


@pytest.mark.asyncio
async def test_llm_dqq_preserves_content_and_time(plugin, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1700000000)
    event = Event()
    content = '数字是 42，\n他说 "不要改变"，还有 空格'
    response = await plugin.tool_dqq(event, content, -432000)
    assert "已立即发送" in response and "未创建定时任务" in response
    node = event.bot.call_action.call_args.kwargs["messages"][0]["data"]
    assert node["time"] == 1699568000
    assert node["content"][0]["data"]["text"] == content
    event.bot.call_action.assert_awaited_once()
    event.send.assert_not_called()
    assert not event.stopped


@pytest.mark.asyncio
async def test_llm_dqq_text_fallback(plugin):
    event = Event(platform="telegram")
    response = await plugin.tool_dqq(event, "Hello")
    assert "普通文本" in response
    assert event.send.call_args.args == ("Hello",)
    event.send.assert_awaited_once()
    event.bot.call_action.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,offset",
    [("", 0), ("hi", 0.5), ("hi", True), ("hi", "60"), ("hi", 999999999999)],
)
async def test_llm_dqq_rejects_invalid_arguments(plugin, content, offset):
    event = Event()
    assert "未发送" in await plugin.tool_dqq(event, content, offset)
    event.bot.call_action.assert_not_called()
    event.send.assert_not_called()


@pytest.mark.asyncio
async def test_llm_failed_send_never_reports_success(plugin):
    event = Event(platform="telegram")
    event.send.side_effect = RuntimeError("send failed")
    assert "失败" in await plugin.tool_divergence(event, "1.048596")
    assert "失败" in await plugin.tool_special(event)
    assert "未完成" in await plugin.tool_timeline(event)
    assert "失败" in await plugin.tool_dqq(event, "Hello")


def test_llm_tool_parameter_docstrings(plugin):
    import inspect

    import docstring_parser

    expected = {
        "tool_divergence": {"divergence": "string"},
        "tool_special": {"divergence": "string"},
        "tool_timeline": {"year": "string"},
        "tool_dqq": {"content": "string", "offset_seconds": "number"},
    }
    for method, params in expected.items():
        func = getattr(plugin, method)
        doc = docstring_parser.parse(func.__doc__)
        assert {arg.arg_name: arg.type_name for arg in doc.params} == params
        assert set(inspect.signature(func).parameters) == {"event", *params}
        assert all(arg.description for arg in doc.params)
