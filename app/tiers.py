# -*- coding: utf-8 -*-
"""速度档位（0.6.0 M3）：**唯一的事实来源**。

清单第 5.1 节的六档映射表就写在 ``TIERS`` 里，签到并发、点赞并发、点赞延迟、
滑块颜色都从这张表读；界面和存储只存 ``{"level": N}`` 这个意图，不存裸数字。
以后换令牌桶 / AIMD 自适应时只改这张表。

0.6.0（M3a）只开放 level 0–3；level 4/5 要等 M3b 的硬停 + 熔断降档一起上，
现在 ``RED_UNLOCKED = False``：服务端拒绝保存红档，界面上红区是灰的、松手会
弹回「极速」。

读取必须 clamp（``max(0, min(5, int(raw)))``）：手改配置文件写成 99 也不能崩，
要压回上限而不是信它。
"""

import collections

TIER_COUNT = 6
DEFAULT_LEVEL = 1
#: 0.6.0 只开放到「极速」；M3b 解锁后把 RED_UNLOCKED 改成 True。
MAX_OPEN_LEVEL = 3
RED_UNLOCKED = False

Tier = collections.namedtuple(
    "Tier", "level name icon sign_concurrency like_concurrency "
            "like_delay color")

#: level, 档名, 图标, 签到并发, 点赞并发, 点赞延迟(秒), 滑块颜色
TIERS = [
    Tier(0, "保守", "", 2, 3, (0.50, 0.80), "blue"),
    Tier(1, "标准", "", 4, 5, (0.25, 0.50), "blue"),
    Tier(2, "激进", "", 6, 8, (0.15, 0.30), "amber"),
    Tier(3, "极速", "", 8, 10, (0.12, 0.25), "amber"),
    Tier(4, "危险", "\U0001F480", 12, 15, (0.08, 0.15), "red"),
    Tier(5, "危险+", "\u2620\ufe0f", 16, 18, (0.05, 0.10), "red"),
]


def clamp(raw):
    """把任意输入压成 0–5 的整数档位；非法值回默认档（标准）。"""
    try:
        level = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    return max(0, min(TIER_COUNT - 1, level))


def get(level):
    return TIERS[clamp(level)]


def max_open_level():
    """当前版本允许保存的最高档位（红档未解锁时是 3）。"""
    return TIER_COUNT - 1 if RED_UNLOCKED else MAX_OPEN_LEVEL


def can_open(level):
    return clamp(level) <= max_open_level()


def like_rate_per_sec(tier):
    """按档位的并发与延迟估算点赞请求速率（次/秒），给令牌桶用。

    延迟是「每条 agree 前的停顿」，再加约 0.1s 的请求往返；这样标准档约
    10.5 次/秒、危险+ 约 102 次/秒，和清单 5.1 的估算一致。
    """
    lo, hi = tier.like_delay
    return tier.like_concurrency / ((lo + hi) / 2.0 + 0.1)
