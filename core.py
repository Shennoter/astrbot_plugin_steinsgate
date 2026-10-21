"""Framework-independent rendering, data and D-QQ parsing."""

import io
import json
import random
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).parent / "resources"
SHANGHAI = timezone(timedelta(hours=8))


def normalize_divergence(value: str) -> str:
    """Return seven tube symbols; '-' means an unlit first tube (original art)."""
    value = value.strip().removesuffix("%")
    if re.fullmatch(r"[0-9]{1,7}", value):
        return value.ljust(7, "0")
    if re.fullmatch(r"-[0-9]{1,6}", value):
        return "-" + value[1:].ljust(6, "0")
    if re.fullmatch(r"[0-9]\.[0-9]{1,6}", value):
        return value.replace(".", "").ljust(7, "0")
    if re.fullmatch(r"-0?\.[0-9]{1,6}", value):
        return "-" + value.split(".")[1].ljust(6, "0")
    raise ValueError(
        "变动率格式错误。示例：/div 1.048596 或 /div 1048596；最多一位整数、六位小数。"
    )


def divergence_label(symbols: str) -> str:
    return f"{symbols[0]}.{symbols[1:]}%"


def render_meter(symbols: str) -> bytes:
    if not re.fullmatch(r"[0-9-][0-9]{6}", symbols):
        raise ValueError("无效的辉光管数字")
    names = [symbols[0], "dot", *symbols[1:]]
    tiles = []
    for name in names:
        with Image.open(ASSETS / f"{'none' if name == '-' else name}.png") as img:
            tiles.append(img.convert("RGB"))
    result = Image.new(
        "RGB", (sum(i.width for i in tiles), max(i.height for i in tiles))
    )
    x = 0
    for tile in tiles:
        result.paste(tile, (x, 0))
        x += tile.width
    output = io.BytesIO()
    result.save(output, "PNG")
    return output.getvalue()


class DataStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("UniDiv.json", "Timeline.json"):
            target = data_dir / name
            if not target.exists():
                shutil.copyfile(ASSETS / name, target)

    def read(self, name: str) -> list[dict]:
        records = json.loads((self.data_dir / name).read_text(encoding="utf-8-sig"))
        keys = (
            ("number", "abstract", "detail")
            if name == "UniDiv.json"
            else ("attractorField", "year", "date", "detail")
        )
        if not isinstance(records, list) or not records:
            raise ValueError(f"{name} 必须是非空数组")
        for item in records:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(k), str) for k in keys
            ):
                raise ValueError(f"{name} 的记录字段不完整或不是字符串")
            if name == "UniDiv.json":
                normalize_divergence(item["number"])
        return records

    def special(self, value: str = "") -> tuple[str, str]:
        entries = self.read("UniDiv.json")
        if value:
            symbols = normalize_divergence(value)
            matches = [
                item
                for item in entries
                if normalize_divergence(item["number"]) == symbols
            ]
            if not matches:
                raise ValueError(
                    "内置资料中没有这条特殊世界线；可使用普通世界线工具只生成图片。"
                )
            item = matches[0]
        else:
            item = random.choice(entries)
        symbols = normalize_divergence(item["number"])
        lines = [divergence_label(symbols)]
        if item["abstract"]:
            lines.append("● " + item["abstract"])
        if item["detail"]:
            lines.extend("○ " + line for line in item["detail"].splitlines())
        return symbols, "\n".join(lines)

    def timeline(self, year: str = "") -> list[str]:
        entries = self.read("Timeline.json")
        entries = (
            [item for item in entries if item["year"] == year]
            if year
            else [random.choice(entries)]
        )
        messages = []
        for item in entries:
            lines = [
                f"世界线收束范围：{item['attractorField']}",
                f"年份：{item['year']}",
            ]
            if item["date"]:
                lines.append(f"日期：{item['date']}")
            lines.append(f"事件：{item['detail']}")
            messages.append("\n".join(lines))
        return messages or ["无此年份"]


def parse_dqq(arguments: str, default_seconds: int = 432000) -> tuple[str, int]:
    """A numeric last word is the offset; quoted content disambiguates numbers."""
    arguments = arguments.strip()
    if not arguments:
        raise ValueError(
            "请输入内容。用法：dqq 内容 [秒数]；带空格或以数字结尾的内容可加双引号。"
        )
    if arguments.startswith(('"', "'")):
        quote = arguments[0]
        end = arguments.find(quote, 1)
        if end < 0:
            raise ValueError("内容的引号未闭合")
        content = arguments[1:end]
        rest = arguments[end + 1 :].strip()
        if rest and not re.fullmatch(r"[+-]?[0-9]{1,12}", rest):
            raise ValueError("时间必须为整数，单位：秒")
        offset = int(rest) if rest else default_seconds
    else:
        parts = arguments.rsplit(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[+-]?[0-9]{1,12}", parts[1]):
            content, offset = parts[0], int(parts[1])
        else:
            content, offset = arguments, default_seconds
    if not content.strip():
        raise ValueError("请输入内容")
    return content, offset


def dqq_timestamp(offset: int, now: int, target_datetime: str = "") -> tuple[int, str]:
    if target_datetime:
        if not isinstance(target_datetime, str):
            raise ValueError("目标日期必须是 ISO 日期字符串，如 2014-12-25")
        try:
            moment = datetime.fromisoformat(
                target_datetime.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "目标日期格式错误，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS"
            ) from exc
        if moment.microsecond:
            raise ValueError("目标时间仅支持整秒")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=SHANGHAI)
        target = int(moment.timestamp())
    else:
        target = now + offset
    # QQ forwarding implementations commonly use signed 32-bit Unix seconds.
    if not 0 <= target <= 2147483647:
        raise ValueError("目标时间须在 1970 年至 2038 年支持的时间范围内")
    date = datetime.fromtimestamp(target, SHANGHAI).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    return target, date
