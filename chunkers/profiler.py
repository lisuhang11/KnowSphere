"""文档画像（DocumentProfile）与自适应策略选择。

文档结构分析（profiler）：
对文档做一次 O(n) 扫描，统计标题层级 / 结构标记等信号，
供 SelectStrategy 决定使用 heading / heuristic / legacy 哪条 tier 链。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

# Markdown 标题行：^#{1,6} 空格 标题
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# 编号章节：1. / 1.2 / 1.2.3 后跟空格、点或制表符
NUMBERED_SECTION_RE = re.compile(r"^\s*\d+(\.\d+){0,4}[.\s\t]+\S")
# 全大写短行（伪标题，常见于 PDF 提取）
ALL_CAPS_LINE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\s\-'’“”:：]{2,58}$")
# 视觉分隔符行：--- / *** / ___ / === 等
VISUAL_SEP_RE = re.compile(r"^\s*([-=_*~])\1{3,}\s*$")
# 章节标记：中文"第X章/节/篇"、英文 Chapter、德文 Kapitel
CHAPTER_MARKER_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百千万0-9]+[章节篇]|"
    r"Chapter\s+\d+[:\s]|Kapitel\s+\d+[:\s])",
    re.IGNORECASE,
)
# 表格行
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
# 代码块围栏
CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# 语言粗略判断（用于诊断展示）
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")

@dataclass
class DocumentProfile:
    """单次扫描得到的文档结构画像。"""

    char_count: int = 0
    line_count: int = 0
    avg_line_len: float = 0.0
    line_len_stddev: float = 0.0
    md_heading_total: int = 0
    md_heading_counts: dict[int, int] = field(default_factory=dict)
    heading_density: float = 0.0
    dominant_heading_level: int = 0
    numbered_section_count: int = 0
    all_caps_short_line_count: int = 0
    form_feed_count: int = 0
    visual_sep_count: int = 0
    chapter_marker_count: int = 0
    repeated_footer_count: int = 0
    has_tables: bool = False
    has_code: bool = False
    detected_langs: list[str] = field(default_factory=list)

    @property
    def heuristic_marker_total(self) -> int:
        """启发式结构标记总数（不含标题）。"""
        return (
            self.numbered_section_count
            + self.all_caps_short_line_count
            + self.visual_sep_count
            + self.chapter_marker_count
            + self.repeated_footer_count
        )

    def to_dict(self) -> dict:
        return {
            "char_count": self.char_count,
            "line_count": self.line_count,
            "avg_line_len": round(self.avg_line_len, 1),
            "line_len_stddev": round(self.line_len_stddev, 1),
            "md_heading_total": self.md_heading_total,
            "md_heading_counts": {
                str(level): count
                for level, count in sorted(self.md_heading_counts.items())
            },
            "heading_density": round(self.heading_density, 4),
            "dominant_heading_level": self.dominant_heading_level,
            "numbered_section_count": self.numbered_section_count,
            "all_caps_short_line_count": self.all_caps_short_line_count,
            "form_feed_count": self.form_feed_count,
            "visual_sep_count": self.visual_sep_count,
            "chapter_marker_count": self.chapter_marker_count,
            "repeated_footer_count": self.repeated_footer_count,
            "heuristic_marker_total": self.heuristic_marker_total,
            "has_tables": self.has_tables,
            "has_code": self.has_code,
            "detected_langs": self.detected_langs,
        }

def _compute_dominant_level(counts: Counter) -> int:
    """主导标题层级：出现>=3次的层级中取最浅（值最小）者；否则取最深。"""
    if not counts:
        return 0
    candidates = [level for level, count in counts.items() if count >= 3]
    if candidates:
        return min(candidates)
    return max(counts)

def profile_document(text: str) -> DocumentProfile:
    """对文档做单次逐行扫描，产出结构画像。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    profile = DocumentProfile(char_count=len(normalized))

    line_lengths: list[int] = []
    heading_counts: Counter = Counter()
    repeated_lines: Counter = Counter()
    in_code = False
    table_streak = 0

    for line in normalized.split("\n"):
        line_lengths.append(len(line))
        stripped = line.strip()
        if not stripped:
            continue

        # Markdown 标题
        m = MD_HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            heading_counts[level] += 1
            profile.md_heading_total += 1

        # 结构标记
        if "\f" in stripped:
            profile.form_feed_count += 1
        if NUMBERED_SECTION_RE.match(line):
            profile.numbered_section_count += 1
        if CHAPTER_MARKER_RE.match(line):
            profile.chapter_marker_count += 1
        if VISUAL_SEP_RE.match(line):
            profile.visual_sep_count += 1
        if ALL_CAPS_LINE_RE.match(line) and any(ch.isalpha for ch in stripped):
            profile.all_caps_short_line_count += 1

        # 表格 / 代码
        if TABLE_LINE_RE.match(line):
            table_streak += 1
            if table_streak >= 2:
                profile.has_tables = True
        else:
            table_streak = 0
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            profile.has_code = True

        # 重复行（页眉/页脚候选），仅统计短行避免误伤
        if len(stripped) <= 60:
            repeated_lines[stripped] += 1

    profile.line_count = len(line_lengths)
    if profile.line_count:
        profile.avg_line_len = sum(line_lengths) / profile.line_count
        if profile.line_count > 1:
            profile.line_len_stddev = statistics.pstdev(line_lengths)
    if profile.md_heading_total:
        profile.heading_density = profile.md_heading_total / profile.line_count
    profile.md_heading_counts = dict(heading_counts)
    profile.dominant_heading_level = _compute_dominant_level(heading_counts)
    profile.repeated_footer_count = sum(
        1 for _line, count in repeated_lines.items() if count >= 3
    )

    # 语言
    if CJK_RE.search(normalized):
        profile.detected_langs.append("zh")
    if LATIN_RE.search(normalized):
        profile.detected_langs.append("en")

    return profile

def select_strategy(profile: DocumentProfile) -> tuple[list[str], str]:
    """根据画像决定 tier 链。返回 (tier_chain, reason)。

    策略选择逻辑：
    - 标题信号充足 → 优先 Tier1 heading
    - 无标题但有结构标记（分页符/编号章节/章节标记等）→ Tier2 heuristic
    - 否则 Tier3 legacy 兜底
    """
    use_heading = (
        profile.md_heading_total >= 3
        and profile.heading_density > 0.005
        and profile.dominant_heading_level > 0
    )
    use_heuristic = (
        profile.heuristic_marker_total >= 5
        or profile.form_feed_count > 0
        or profile.chapter_marker_count > 0
    )
    if use_heading and use_heuristic:
        return ["heading", "heuristic", "legacy"], "检测到标题层级与结构标记，优先按标题切分"
    if use_heading:
        return ["heading", "legacy"], "检测到 Markdown 标题层级，按标题切分"
    if use_heuristic:
        return ["heuristic", "legacy"], "无标题但存在结构标记（分页符/章节等），按启发式边界切分"
    return ["legacy"], "无显著结构信号，使用递归字符切分"
