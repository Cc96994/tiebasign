#!/usr/bin/env bash
# check_history_secrets.sh - 扫 git 历史里的 BDUSS / 凭据泄漏。
#
# 为什么单独有它：leak_scan.sh 只扫**当前工作树**。凭据一旦提交过，即使后来删掉，
# 也还留在历史里（GitHub 上任何人都能翻到）。v0.5.0 要求「检查之前有无泄露风险」，
# 所以把历史也纳入门禁。
#
# 输出只有提交数、命中行数、可疑文件名数，**绝不打印命中内容**。
#
# 用法：
#   bash scripts/check_history_secrets.sh [repo-root]
# 退出码：0 = CLEAN，1 = 有命中，2 = 跳过（不是 git 仓库）。
set -uo pipefail

ROOT="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"

if ! command -v git >/dev/null 2>&1; then
    echo "SKIP  git not available"
    exit 2
fi
if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    echo "SKIP  not a git checkout: $ROOT"
    exit 2
fi

# 与 leak_scan.sh 的 PAT 同一形状：BDUSS= 或 BDUSS%3D 后接 16+ 个 base64 字符。
PAT='BDUSS(=|%3D)[A-Za-z0-9+/=]{16,}'
# 文件名规则：旧版把裸 base64 放在 BDUSS.txt 里，值正则抓不到文件名。
NAME_PAT='BDUSS'

commits="$(git -C "$ROOT" rev-list --all 2>/dev/null | wc -l | tr -d ' ')"
hits="$(git -C "$ROOT" log -p --all --no-color 2>/dev/null \
        | grep -a -c -E "$PAT" || true)"
file_hits="$(git -C "$ROOT" rev-list --objects --all 2>/dev/null \
             | grep -a -c -E "$NAME_PAT" || true)"
hits="${hits:-0}"
file_hits="${file_hits:-0}"

echo "repo    : $ROOT"
echo "commits : $commits"
echo "value-pattern lines : $hits"
echo "BDUSS-named paths   : $file_hits"

if [ "$commits" -eq 0 ]; then
    echo "SKIP  repository has no commits"
    exit 2
fi
if [ "$hits" -gt 0 ] || [ "$file_hits" -gt 0 ]; then
    echo "RESULT: FAIL (history contains credential-shaped data or filenames)"
    exit 1
fi
echo "RESULT: CLEAN"
exit 0
