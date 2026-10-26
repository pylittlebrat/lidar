"""词边界匹配测试：根治 hil/rl/usd 等短词子串误伤。"""

from __future__ import annotations

from techradar.processors.filter import KeywordFilter
from techradar.storage.models import TechItem
from techradar.utils.text import count_matches, match_term


def make_item(title: str, abstract: str = "") -> TechItem:
    return TechItem(source_type="github", source_name="x", title=title,
                    url=f"https://e.example/{abs(hash(title))}", abstract=abstract)


def test_word_boundary_short_terms() -> None:
    # HIL / SIL 不再因 while / similar 等子串误命中
    assert match_term("hil", "while simulating gravity") is False
    assert match_term("hil", "run hil test") is True
    assert match_term("sil", "silent") is False
    assert match_term("sil", "sil test") is True
    # rl 不再命中 world
    assert match_term("rl", "world model") is False
    assert match_term("rl", "rl training") is True
    # 带空格短语整词匹配
    assert match_term("physics engine", "multi-physics engine backend") is True
    assert match_term("physics engine", "physics backpatcher") is False
    # 中文退化为子串匹配
    assert match_term("叉车", "电动叉车仿真") is True


def test_count_matches() -> None:
    assert count_matches(["hil", "rl", "physics"], "while rl physics") == 2


def test_filter_uses_word_boundary_and_negative(config) -> None:
    filt = KeywordFilter(config)
    # 游戏类负向词命中 -> 丢弃（即使子串里曾出现过 hil）
    game = make_item(
        "Universe Sandbox 2 game download",
        "free setup while simulating gravity and climate ecosystems",
    )
    kept, dropped = filt.apply([game])
    assert dropped == [game]
    # 无负向词、无优先级词命中 -> 丢弃；watch 白名单仍放行
    plain = make_item("A generic title", "unrelated noise text")
    _, dropped2 = filt.apply([plain])
    assert dropped2 == [plain]
    watched = make_item("Anything at all", "zebras")
    watched.metadata["watched"] = True
    kept3, _ = filt.apply([watched])
    assert kept3 == [watched]