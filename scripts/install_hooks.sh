#!/usr/bin/env sh
# 安装本地 git 钩子。
#
# 装了什么：pre-commit 钩子，在每次提交前跑一遍凭证扫描。
# 为什么需要：.gitignore 只能挡住已知路径，挡不住「把 token 写进了本该提交的文件里」。
#             钩子是最后一道自动防线，不依赖人记得手动跑。
#
# 用法：
#     sh scripts/install_hooks.sh
#
# 钩子只装在本机（.git/hooks/ 不进版本库），所以每个克隆都需要跑一次。

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"

if [ ! -d "$REPO_ROOT/.git" ]; then
    echo "错误：当前目录不是 git 仓库。" >&2
    exit 1
fi

cat > "$HOOK" <<'HOOK_BODY'
#!/usr/bin/env sh
# vidrelay pre-commit：提交前扫描凭证泄露。
# 由 scripts/install_hooks.sh 生成。绕过方式：git commit --no-verify

PYTHON=""
for candidate in ".venv/bin/python" ".venv/Scripts/python.exe" "python3" "python"; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
    if [ -x "$candidate" ]; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "pre-commit: 找不到 Python，跳过凭证扫描。" >&2
    exit 0
fi

"$PYTHON" scripts/scan_secrets.py --staged
status=$?

if [ $status -ne 0 ]; then
    echo ""
    echo "提交已被阻止：扫描到高危凭证泄露。"
    echo "清理后重新提交；确认为误报可在该行加 # scan:allow 标记。"
    echo "（确实需要绕过：git commit --no-verify）"
fi

exit $status
HOOK_BODY

chmod +x "$HOOK" 2>/dev/null || true
echo "已安装 pre-commit 钩子：$HOOK"
echo "试一下：修改任意文件后 git add，再执行 git commit，会先跑凭证扫描。"
