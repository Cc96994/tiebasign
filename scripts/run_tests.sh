#!/usr/bin/env bash
# run_tests.sh - 离线自检总入口（不联网、不碰 Android、不写进项目目录）。
#
# 前三个套件是纯 Python + 桩，后两个是 bash；桌面/WSL 上都能跑，几秒钟出结果。
#   1. test_updater    检查更新：解析 / 版本判定 / 校验 / 每日调度
#   2. test_waterpost  自动水贴：链接 / 文案 / 每账号分组计划 / 手动一键水贴
#   3. test_ui_update  界面接线：所有页面 × 四种更新状态渲染 + 真 HTTP 打接口
#   4. test_build_swap 构建脚本：spec 交换在"新克隆 / 构建失败"下不许丢原件、
#                      也不许把换进去的 spec 留在树里
#   5. check_p4a_base  底座可重建：净室重建 p4a 底座，断言 App 反射调用的每个
#                      Java 方法都有定义（底座在仓库之外，最容易静默丢失）
#
# verify_release.sh 会调用本脚本 —— 发布前必须先过这一关。
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd -- "$SCRIPT_DIR/.." && pwd)"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    PY=python
fi
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "run_tests.sh: no python3/python on PATH" >&2
    exit 2
fi

fail=0
for t in test_updater test_waterpost test_ui_update; do
    echo "=================== $t ==================="
    if "$PY" "$PROJ/scripts/tests/$t.py"; then
        :
    else
        echo "!! $t FAILED"
        fail=1
    fi
    echo
done

# 第四个套件是 shell：它测的是构建脚本本身，用 bash 跑，和 Python 无关。
# 之所以要有它：新克隆的树里没有 buildozer.spec（历史投影 spec，在 .gitignore
# 里），旧写法在 set -e 下第一条 cp 就死，而且构建失败还会把换进去的 spec
# 留在树里 —— 两个都是"只有克隆的人才会撞上"的静默故障。
echo "=================== test_build_swap ==================="
if bash "$PROJ/scripts/tests/test_build_swap.sh"; then
    :
else
    echo "!! test_build_swap FAILED"
    fail=1
fi
echo

# 第五个套件：p4a 底座在仓库之外，重新解压一次就可能让原生登录/保活/安装入口
# 静默消失（构建照样成功）。它在临时目录里净室重建并断言，只读、不碰 dists。
echo "=================== check_p4a_base ==================="
if bash "$PROJ/scripts/check_p4a_base.sh"; then
    :
else
    echo "!! check_p4a_base FAILED"
    fail=1
fi
echo

if [ "$fail" = 0 ]; then
    echo "ALL_TESTS_OK"
else
    echo "ALL_TESTS_FAILED"
fi
exit "$fail"
