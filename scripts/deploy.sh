#!/usr/bin/env bash
# 把当前分支部署到 192.168.1.38。
#
# 2026-09-03 起走 GitHub，不再走本机裸仓库：
#
#   本地 --push--> GitHub 私有仓库 --fetch--> 服务器
#
# 服务器上配了持久凭据（`credential.helper=store`，token 在 ~/.git-credentials，
# 权限 600），所以 HTTPS 能直接拉私有仓库，不用交互输密码。
# **SSH 那条路不通**——服务器的 known_hosts 里没有 github.com，
# `git@github.com:` 形式会报 Host key verification failed。别改成 SSH。
#
# 另外两个容易踩的地方：
#
# 1. 本机的 `server` remote（tao@192.168.1.38:/home/tao/git/tt-trading-mcp.git）
#    和服务器上那个裸仓库都还留着，但**日常部署不再用它们**了。
#    留着是当后备：GitHub 不通的时候还能
#    `git push server <分支>` 再上去 `git fetch /home/tao/git/tt-trading-mcp.git`。
# 2. tt-mcp 是【用户级】systemd 服务，跟这台机器上的 sos / v5-telegram 一个路子。
#    systemctl 必须带 --user，漏了会去找系统级的同名服务，报 not found。
# 3. tt-watchdog（盯盘守护进程）也要一起重启。它跟 tt-mcp 是两个独立进程，
#    只重启 tt-mcp 的话，部署完新代码盯盘还在跑旧的，而且**没有任何提示**。
set -euo pipefail

HOST="tao@192.168.1.38"
DIR="/home/tao/tt-trading-mcp"
REPO="https://github.com/giordanoshai/tt-ai-screener.git"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "==> 推 ${BRANCH} 到 GitHub"
git push origin "${BRANCH}"

echo "==> 服务器从 GitHub 拉取、装依赖、重启"
# 每次都把 origin 校正一遍：这台机器上的仓库历史上指过本机裸仓库，
# 指错了的话下面 fetch 到的是一份过期代码，而且不会报错。
ssh "${HOST}" "cd ${DIR} \
  && git remote set-url origin ${REPO} \
  && GIT_TERMINAL_PROMPT=0 git fetch origin \
  && git checkout -B ${BRANCH} origin/${BRANCH} \
  && .venv/bin/pip install -q -r requirements.txt \
  && systemctl --user restart tt-mcp \
  && { systemctl --user restart tt-watchdog || echo '  !! tt-watchdog 重启失败（多半是还没装），盯盘仍在跑旧代码；装法见 scripts/tt-watchdog.service 顶部注释'; }"

echo "==> 验证"
ssh "${HOST}" "systemctl --user is-active tt-mcp; systemctl --user is-active tt-watchdog; cd ${DIR} && git log --oneline -1"
# 服务绑的是 0.0.0.0，这里仍然走 127.0.0.1，确认是服务自己在应答
ssh "${HOST}" "curl -s -o /dev/null -w '  首页      %{http_code}  (应 200)\n' http://127.0.0.1:8766/"
ssh "${HOST}" "curl -s -o /dev/null -w '  错 token  %{http_code}  (应 401)\n' 'http://127.0.0.1:8766/mcp?token=WRONG'"
echo "==> 完成"
