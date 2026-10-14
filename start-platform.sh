#!/bin/sh
# 起平台后端. 前端由它同源托管 (包里的 frontend/dist), 故部署形态只有这一个进程.
#
# 虚拟环境**不在这里自动建**: 建环境要联网装依赖, 那是部署步骤 (见 docs/deployment.md),
# 不该藏在"起服务"里. 这里只保证路径与解释器是对的.

set -eu

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    printf '%s\n' "还没有虚拟环境. 先在这个目录下跑:" "    python3 -m venv .venv" "    ./.venv/bin/python -m pip install -r requirements.deploy.txt" >&2
    exit 1
fi

cd backend

exec ../.venv/bin/python -m app.main
