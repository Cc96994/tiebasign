# -*- coding: utf-8 -*-
"""速度档位（0.6.0 M3 / 0.6.4 调整）：**唯一的事实来源**。

签到和点赞是两套独立的档位：

* 签到：清单第 5.1 节的六档 ``TIERS``（并发 2/4/6/8/12/16）；
* 点赞（0.6.4 按用户要求）：只有四档 ``LIKE_TIERS``，并发 3/5/8/12，
  颜色 绿/绿/黄/红。点赞的速率不再由「并发 ÷ 延迟」估算，而是每一档
  显式给一个 ``rate``（次/秒），点赞侧再叠加 AIMD 自适应：先按保守速率跑，
  没撞 3280004 才逐次 +1，撞了就减半并落盘。

读取必须 clamp：手改配置文件写成 99 也不能崩，要压回上限而不是信它。
"""

import collections

# ---------------------------------------------------------------- 签到档位

TIER_COUNT = 6
DEFAULT_LEVEL = 1
#: 未解锁时只开放到「极速」；0.6.0 已解锁红档。
MAX_OPEN_LEVEL = 3
RED_UNLOCKED = True

Tier = collections.namedtuple(
    "Tier", "level name icon sign_concurrency like_concurrency "
            "like_delay color")

#: level, 档名, 图标, 签到并发, 点赞并发(旧字段), 点赞延迟(旧字段), 滑块颜色
TIERS = [
    Tier(0, "保守", "", 2, 3, (0.50, 0.80), "blue"),
    Tier(1, "标准", "", 4, 5, (0.25, 0.50), "blue"),
    Tier(2, "激进", "", 6, 8, (0.15, 0.30), "amber"),
    Tier(3, "极速", "", 8, 10, (0.12, 0.25), "amber"),
    Tier(4, "危险", "\U0001F480", 12, 15, (0.08, 0.15), "red"),
    Tier(5, "危险+", "\u2620\ufe0f", 16, 18, (0.05, 0.10), "red"),
]

# ---------------------------------------------------------------- 点赞档位

LIKE_TIER_COUNT = 4
LIKE_DEFAULT_LEVEL = 1

LikeTier = collections.namedtuple(
    "LikeTier", "level name icon concurrency delay rate color")

#: level, 档名, 图标, 点赞并发, 点赞前停顿, 目标速率(次/秒), 滑块颜色
#:
#: 四档都开放；第四档红色，保存前仍要二次确认。concurrency 是用户明确
#: 指定的 3/5/8/12；rate 是点赞侧令牌桶的上限，实际速率还会被 AIMD 下调。
LIKE_TIERS = [
    LikeTier(0, "一档", "", 3, (0.60, 0.90), 4.0, "green"),
    LikeTier(1, "二档", "", 5, (0.45, 0.70), 7.0, "green"),
    LikeTier(2, "三档", "", 8, (0.35, 0.55), 10.0, "amber"),
    LikeTier(3, "四档", "", 12, (0.28, 0.45), 12.0, "red"),
]


def clamp(raw):
    """把任意输入压成 0–5 的签到档位；非法值回默认档（标准）。"""
    try:
        level = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    return max(0, min(TIER_COUNT - 1, level))


def get(level):
    return TIERS[clamp(level)]


def max_open_level():
    """当前版本允许保存的最高签到档位（红档未解锁时是 3，0.6.0 是 5）。"""
    return TIER_COUNT - 1 if RED_UNLOCKED else MAX_OPEN_LEVEL


def can_open(level):
    return clamp(level) <= max_open_level()


def clamp_like(raw):
    """把任意输入压成 0–3 的点赞档位；非法值回默认档（二档）。"""
    try:
        level = int(raw)
    except (TypeError, ValueError):
        return LIKE_DEFAULT_LEVEL
    return max(0, min(LIKE_TIER_COUNT - 1, level))


def get_like(level):
    return LIKE_TIERS[clamp_like(level)]


def max_like_level():
    return LIKE_TIER_COUNT - 1


def like_rate_per_sec(tier):
    """点赞档位的目标速率（次/秒）。兼容旧的 Tier 对象。"""
    rate = getattr(tier, "rate", None)
    if rate:
        return float(rate)
    lo, hi = tier.like_delay
    return tier.like_concurrency / ((lo + hi) / 2.0 + 0.1)
