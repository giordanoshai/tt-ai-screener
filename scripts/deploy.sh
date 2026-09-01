#!/usr/bin/env bash
# 把当前分支部署到 192.168.1.38。
#
# 两个容易踩的地方：
#
# 1. 服务器上的 origin 是本机的裸仓库（~/git/tt-trading-mcp.git），不是 GitHub。
#    裸仓库没装钩子，所以「推」和「拉」是两步，推完必须再 ssh 上去拉一次。
#
# 2. tt-mcp 是【用户级】systemd 服务，跟这台机器上的 sos / v5-telegram 一个路子。
#    systemctl 必须带 --user，漏了会去找系统级的同名服务，报 not found。
set -euo pipefail

HOST="tao@192.168.1.38"
DIR="/home/tao/tt-trading-mcp"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "==> 推 ${BRANCH} 到服务器裸仓库"
git push server "${BRANCH}"

echo "==> 服务器拉取、装依赖、重启"
ssh "${HOST}" "cd ${DIR} \
  && git fetch origin \
  && git checkout -B ${BRANCH} origin/${BRANCH} \
  && .venv/bin/pip install -q -r requirements.txt \
  && systemctl --user restart tt-mcp"

echo "==> 验证"
ssh "${HOST}" "systemctl --user is-active tt-mcp; cd ${DIR} && git log --oneline -1"
# 服务绑的是 0.0.0.0，这里仍然走 127.0.0.1，确认是服务自己在应答
ssh "${HOST}" "curl -s -o /dev/null -w '  首页      %{http_code}  (应 200)\n' http://127.0.0.1:8766/"
ssh "${HOST}" "curl -s -o /dev/null -w '  错 token  %{http_code}  (应 401)\n' 'http://127.0.0.1:8766/mcp?token=WRONG'"
echo "==> 完成"
