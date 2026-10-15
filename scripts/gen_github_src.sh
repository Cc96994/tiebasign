#!/usr/bin/env bash
# gen_github_src.sh - 从当前树重新生成 github_src/（GitHub 上传暂存目录）。
#
# 为什么需要它：github_src/ 是 v0.3.0 时期手工整理出来的上传副本，到 0.4.0 已经
# 严重过期 —— app/ 里没有 waterpost.py / updater.py，scripts/ 还是那 7 个带失效
# Windows 路径的旧脚本，.gitignore 也是旧的。而"手工整理"正是它过期的原因：
# 没有脚本，它必然再次过期。所以清单写死在下面，每次发布前重新生成一次。
#
# 它同时是一道**安全闸门**：暂存目录里出现密钥、登录态数据库、日志或任何名字带
# BDUSS 的文件，就直接判失败。GitHub 网页拖拽会绕过（并丢掉）`.gitignore`，
# 所以"哪些文件该上传"不能只靠 .gitignore 表达。
#
# 用法：
#   bash scripts/gen_github_src.sh            # 重新生成 github_src/
#   bash scripts/gen_github_src.sh --check    # 只报告，不动 github_src/
# Exit: 0 = 生成/检查通过, 1 = 有禁止项或必需文件缺失
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd -- "$SCRIPT_DIR/.." && pwd)"
DST="$PROJ/github_src"
NEW="$PROJ/.github_src.new"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

bad=0
fail() { echo "FAIL  $*" >&2; bad=1; }

[ -d "$PROJ/app" ] || { echo "FAIL  not the project root: $PROJ" >&2; exit 1; }

# ---- 1. 收集清单（缺失=失败，绝不静默少传） ---------------------------------
rm -rf "$NEW"; mkdir -p "$NEW"
n_files=0
put() {  # <absolute source path>, destination keeps its path relative to PROJ
    local src="$1" rel="${1#$PROJ/}" dst="$NEW/${1#$PROJ/}"
    if [ ! -f "$src" ]; then fail "required file missing: $rel"; return 0; fi
    mkdir -p "$(dirname -- "$dst")"
    cp -a "$src" "$dst"
    n_files=$((n_files + 1))
}
put_tree() {  # <dir relative to PROJ>
    local d="$PROJ/$1"
    if [ ! -d "$d" ]; then fail "required directory missing: $1/"; return 0; fi
    local f
    while IFS= read -r -d '' f; do put "$f"; done < <(find "$d" -type f -print0)
}

echo "== collecting the upload set"
# 顶层文档与构建配置
for f in .gitignore LICENSE buildozer_webview_app.spec README.md ROADMAP.md CREDITS.md \
         GITHUB_PREP.md SIGNING.md PRD_ACCEPTANCE_0.3.0.md; do
    put "$PROJ/$f"
done
# 应用源码 + 图标（app/ 下的 *.py 全要；数据库/日志由下面的闸门挡掉）
while IFS= read -r -d '' f; do put "$f"; done < <(
    find "$PROJ/app" -maxdepth 1 -type f \( -name '*.py' -o -name 'icon.png' \) -print0)
# p4a 本地 recipe（构建链的一部分）
put_tree "p4a_local"
# 脚本（含自检套件）
put_tree "scripts"
# 仓库自己拥有的 Java（构建输入，曾经被 .gitignore 误忽略）
put_tree "java_src"
# p4a 底座材料：pristine 源码包 + 手工改动快照（见 third_party/README.md）
put_tree "third_party"
# 离线 sdist 备份：aiotieba 4.7.1 是**锁死的**依赖（4.8.1 删掉了 add_post），
# 把 sdist 放进仓库可以让构建不依赖上游 GitHub 是否还在提供服务。
while IFS= read -r -d '' f; do put "$f"; done < <(
    find "$PROJ/vendor" -maxdepth 1 -type f -name '*.tar.gz' -print0 2>/dev/null)

# 这些**必须**在暂存集里 —— 它们正是这条债的两端
for must in java_src/org/dsh/tiebaweb/ApkProvider.java \
            java_src/PythonActivity.updater.inc \
            third_party/p4a-dev.tar.gz \
            third_party/p4a-webview-base-overlay.tar.gz \
            .gitignore LICENSE; do
    [ -f "$NEW/$must" ] || fail "$must is not in the upload set"
done
n_scripts=$(find "$NEW/scripts" -type f 2>/dev/null | wc -l)
[ "$n_scripts" -ge 10 ] || fail "only $n_scripts scripts staged - the collector dropped files"
n_files_total=$(find "$NEW" -type f | wc -l)
[ "$n_files_total" -ge 25 ] || fail "only $n_files_total files staged - refusing to call that a release set"

# ---- 2. 安全闸门 -----------------------------------------------------------
echo "== deny rules"
denied=0
while IFS= read -r -d '' f; do
    rel="${f#$NEW/}"
    case "$rel" in
        sign/*|*.keystore|*.jks|*signing.env) fail "secret staged: $rel"; denied=$((denied+1)) ;;
        app/tiebasign.db*|app/tiebasign_accounts.json*) fail "runtime data staged: $rel"; denied=$((denied+1)) ;;
        *.log|*.pyc|*/__pycache__/*) fail "build/runtime artifact staged: $rel"; denied=$((denied+1)) ;;
        *BDUSS*|*bduss*) fail "credential-looking filename staged: $rel"; denied=$((denied+1)) ;;
    esac
done < <(find "$NEW" -type f -print0)
[ "$denied" -eq 0 ] && echo "   no secret / runtime-data / artifact / credential filenames"

# 值扫描（裸的 BDUSS 值抓不到"文件名"规则，所以两条都要）
hits="$(grep -rl -a -E 'BDUSS(=|%3D)[A-Za-z0-9+/=]{16,}' "$NEW" 2>/dev/null || true)"
if [ -n "$hits" ]; then
    echo "$hits" | sed "s|$NEW/|      |" | sed 's/^/   HIT  /'
    fail "BDUSS value pattern found in $(printf '%s\n' "$hits" | wc -l) staged file(s)"
else
    echo "   no BDUSS value pattern"
fi

# ---- 3. 报告 / 落盘 --------------------------------------------------------
total="$(find "$NEW" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}')"
echo
echo "== staged set: $n_files_total files, $total bytes"
(cd "$NEW" && find . -type f | sed 's|^\./||' | sort | sed 's/^/   /')

if [ "$bad" != 0 ]; then
    rm -rf "$NEW"
    echo
    echo "GEN_GITHUB_SRC_FAILED"
    exit 1
fi

if [ "$CHECK_ONLY" = 1 ]; then
    rm -rf "$NEW"
    echo
    echo "GEN_GITHUB_SRC_CHECK_OK (github_src/ left untouched)"
    exit 0
fi

# 只在确认 DST 确实是我们的暂存目录时才替换它（永不盲删一个算错的路径）
if [ -e "$DST" ] && [ ! -f "$DST/GITHUB_PREP.md" ] && [ -n "$(ls -A "$DST" 2>/dev/null)" ]; then
    rm -rf "$NEW"
    echo "FAIL  $DST exists but does not look like the staging dir (no GITHUB_PREP.md); refusing to delete it" >&2
    echo "GEN_GITHUB_SRC_FAILED"
    exit 1
fi
rm -rf "$DST"
mv "$NEW" "$DST"
echo
echo "== wrote $DST"
echo "GEN_GITHUB_SRC_OK"
