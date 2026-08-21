#!/bin/bash

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 强制拷贝同级目录下的 app 文件夹，覆盖到 /opt/monitor_agent/ 下
cp -rf "$SCRIPT_DIR/app" /opt/monitor_agent/