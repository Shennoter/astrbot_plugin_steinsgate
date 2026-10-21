import io
import json

import pytest
from astrbot_plugin_steinsgate.core import (
    ASSETS,
    DataStore,
    dqq_timestamp,
    normalize_divergence,
    parse_dqq,
    render_meter,
)
from PIL import Image


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.048596", "1048596"),
        ("1048596", "1048596"),
        ("0.1%", "0100000"),
        ("1", "1000000"),
        ("-275349", "-275349"),
        ("-0.275349", "-275349"),
        ("-.2", "-200000"),
    ],
)
def test_divergence(value, expected):
    assert normalize_divergence(value) == expected


@pytest.mark.parametrize(
    "value", ["", "abc", "12.34", "1.1234567", "12345678", "-1.2", "nan", "../0"]
)
def test_bad_divergence(value):
    with pytest.raises(ValueError):
        normalize_divergence(value)


def test_render_geometry_and_pixels():
    image = Image.open(io.BytesIO(render_meter("1048596")))
    names = ["1", "dot", "0", "4", "8", "5", "9", "6"]
    tiles = [Image.open(ASSETS / f"{name}.png").convert("RGB") for name in names]
    assert image.size == (
        sum(tile.width for tile in tiles),
        max(tile.height for tile in tiles),
    )
    x = 0
    for tile in tiles:
        assert (
            image.crop((x, 0, x + tile.width, tile.height)).tobytes() == tile.tobytes()
        )
        x += tile.width


def test_all_original_special_entries_render(tmp_path):
    store = DataStore(tmp_path)
    entries = store.read("UniDiv.json")
    for item in entries:
        Image.open(
            io.BytesIO(render_meter(normalize_divergence(item["number"])))
        ).verify()
    assert len(entries) > 20


def test_year_and_preserved_custom_data(tmp_path):
    store = DataStore(tmp_path)
    source = store.read("Timeline.json")
    expected = [x for x in source if x["year"] == "2010"]
    assert len(store.timeline("2010")) == len(expected) > 1
    assert all("年份：2010" in text for text in store.timeline("2010"))
    assert store.timeline("9999") == ["无此年份"]
    assert len(store.timeline()) == 1
    custom = [{"number": "1048596", "abstract": "自定义", "detail": "第一行\n第二行"}]
    (tmp_path / "UniDiv.json").write_text(json.dumps(custom), encoding="utf-8")
    DataStore(tmp_path)
    symbols, text = store.special()
    assert symbols == "1048596"
    assert "● 自定义" in text and "○ 第二行" in text
    (tmp_path / "UniDiv.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        store.special()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("消息", ("消息", 432000)),
        ("来自 过去 -432000", ("来自 过去", -432000)),
        ('"数字是 42"', ("数字是 42", 432000)),
        ('"多 空格  消息" +60', ("多 空格  消息", 60)),
        ("hello there", ("hello there", 432000)),
        ("42", ("42", 432000)),
    ],
)
def test_dqq_parser(args, expected):
    assert parse_dqq(args) == expected


@pytest.mark.parametrize("args", ["", '""', '"未闭合', '"消息" abc'])
def test_dqq_invalid(args):
    with pytest.raises(ValueError):
        parse_dqq(args)


def test_time_offset():
    target, label = dqq_timestamp(-432000, 1700000000)
    assert target == 1699568000
    assert label == "2023-11-10 06:13:20 UTC+8"
    with pytest.raises(ValueError):
        dqq_timestamp(999999999999, 1700000000)
