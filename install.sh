#!/bin/bash
CUR_PATH=$(dirname "$(readlink -f "$0")")
AGENT_ROOT="/opt/monitor_agent"
AGENT_BIN="${AGENT_ROOT}/bin"
VENV_PATH="${AGENT_ROOT}/venv"
AGENT_LIB="${AGENT_ROOT}/lib"
LOG_PATH="/var/log/monitor_agent"
BIND_IP="0.0.0.0"
OFFLINE=0
AGENT_USER="fsgeeker"
AGENT_GROUP="fsgeeker"
PYTHON_ROOT="/opt/python"

die() { echo "$1"; exit 1; }

usage() {
    script_name=$(basename "$0")
    echo "Usage: ./${script_name} [-i 0.0.0.0] [--offline]"
    echo "  -i, --ip        bind local ip addr, default 0.0.0.0"
    echo "  -o, --offline   install Python deps from local wheels/"
    echo "  -h, --help      show this help"
    exit 0
}

disable_selinux() {
    if [ -f /etc/selinux/config ]; then
        CHECK=$(grep SELINUX= /etc/selinux/config | grep -v "#")
        case $CHECK in
            "SELINUX=enforcing"|"SELINUX=permissive")
                sed -i 's/SELINUX=.*/SELINUX=disabled/g' /etc/selinux/config
                setenforce 0 2>/dev/null || true
                ;;
        esac
    fi
}

env_check() {
    systemctl stop monitor_agent.service 2>/dev/null
    systemctl stop monitor_agentwatching.timer 2>/dev/null

    # Monitor Agent 使用固定用户/用户组运行；不存在时自动创建
    getent group ${AGENT_GROUP} >/dev/null 2>&1 || groupadd -r ${AGENT_GROUP}
    id -u ${AGENT_USER} >/dev/null 2>&1 || useradd -r -g ${AGENT_GROUP} -s /sbin/nologin ${AGENT_USER}

    rm -rf ${AGENT_ROOT} >/dev/null 2>&1
    mkdir -p ${AGENT_ROOT} ${AGENT_BIN} ${VENV_PATH} ${LOG_PATH} ${AGENT_LIB} ${AGENT_ROOT}/conf ${AGENT_ROOT}/scripts
}

install_python_deps_online() {
    python3 -m venv ${VENV_PATH} || die "创建 venv 失败，请安装 python3-venv"
    ${VENV_PATH}/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip || die "在线升级 pip 失败"
    ${VENV_PATH}/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r ${CUR_PATH}/requirements.txt || die "在线安装 requirements.txt 失败"
}

install_python_venv_offline() {
    if [ "${OS_TYPE}" = "ubuntu" ]; then
        DEB_DIR="${CUR_PATH}/apt_debs"
        [ -d "${DEB_DIR}" ] || die "缺少 Python 离线 deb 包目录: ${DEB_DIR}"
        echo "=== Installing bundled Python packages from local deb packages ==="
        dpkg -i "${DEB_DIR}"/*.deb || die "离线升级 Python 系统组件失败"
    elif [ "${OS_TYPE}" = "centos" ]; then
        PY_BUNDLE=$(find "${CUR_PATH}/python" -mindepth 1 -maxdepth 1 -type d | head -1)
        [ -n "${PY_BUNDLE}" ] || die "缺少 Python 离线基础环境目录: ${CUR_PATH}/python"
        PY_FULL_VER=$(basename "${PY_BUNDLE}")
        mkdir -p "${PYTHON_ROOT}"
        rm -rf "${PYTHON_ROOT}/${PY_FULL_VER}"
        cp -a "${PY_BUNDLE}" "${PYTHON_ROOT}/${PY_FULL_VER}" || die "复制 Python 基础环境失败"
        chown -R root:root "${PYTHON_ROOT}/${PY_FULL_VER}"
        chmod 755 "${PYTHON_ROOT}" "${PYTHON_ROOT}/${PY_FULL_VER}"
        [ -x "${PYTHON_ROOT}/${PY_FULL_VER}/bin/python3" ] || die "离线 Python 不可执行"
        OFFLINE_PYTHON="${PYTHON_ROOT}/${PY_FULL_VER}/bin/python3"
        echo "=== Installed bundled Python to ${PYTHON_ROOT}/${PY_FULL_VER} ==="
    fi
}

install_python_deps_offline() {
    # 离线模式按系统类型安装/释放打包好的 Python 基础环境。
    install_python_venv_offline

    if [ "${OS_TYPE}" = "centos" ]; then
        ${OFFLINE_PYTHON} -m venv ${VENV_PATH} || die "创建 venv 失败"
    else
        command -v python3 >/dev/null || die "未找到 python3，现场需先安装 Python"
        python3 -m venv ${VENV_PATH} || die "创建 venv 失败"
    fi

    WHEEL_DIR="${CUR_PATH}/wheels"
    REQ_LOCK="${CUR_PATH}/requirements.lock.txt"
    REQ="${CUR_PATH}/requirements.txt"
    PIP="${VENV_PATH}/bin/pip"

    [ -d "${WHEEL_DIR}" ] || die "缺少离线包目录: ${WHEEL_DIR}"

    ${PIP} install --upgrade --no-index --find-links="${WHEEL_DIR}" pip setuptools wheel \
        || die "离线安装 pip/setuptools/wheel 失败"

    if [ -f "${REQ_LOCK}" ]; then
        ${PIP} install --no-index --find-links="${WHEEL_DIR}" -r "${REQ_LOCK}" \
            || die "离线安装 requirements.lock.txt 失败"
    else
        ${PIP} install --no-index --find-links="${WHEEL_DIR}" -r "${REQ}" \
            || die "离线安装 requirements.txt 失败"
    fi

    ${VENV_PATH}/bin/python -c "import fastapi, icmplib, pandas, uvicorn; print('deps ok')" \
        || die "依赖导入检查失败"
}

install_python_deps() {
    if [ "${OFFLINE}" -eq 1 ]; then
        echo "=== Offline install: using ${CUR_PATH}/wheels ==="
        install_python_deps_offline
    else
        echo "=== Online install: using requirements.txt ==="
        install_python_deps_online
    fi
}

create_start_script() {
    cat << EOF > ${AGENT_BIN}/start_agent.sh
#!/bin/bash
cd ${AGENT_ROOT}
${VENV_PATH}/bin/uvicorn app.main:app --host ${BIND_IP} --port 20000 --workers 1
EOF
    chmod +x ${AGENT_BIN}/start_agent.sh
}

create_main_service() {
    cat << EOF > /usr/lib/systemd/system/monitor_agent.service
[Unit]
Description=Monitor Agent Service
After=network.target redis.service

[Service]
User=${AGENT_USER}
Group=${AGENT_GROUP}
WorkingDirectory=${AGENT_ROOT}
ExecStart=${AGENT_BIN}/start_agent.sh
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
}

redis_check() {
    if systemctl is-active --quiet redis; then
        echo "Redis has installed"
    else
        echo "Redis is installing..."
        install_depend_redis
    fi
}

install_depend_redis() {
    id -u redis &>/dev/null || useradd -r redis -s /sbin/nologin
    mkdir -p ${AGENT_LIB}
    # 假设你的 3rd/redis.tar.gz 存在
    if [ -f ${CUR_PATH}/3rd/redis.tar.gz ]; then
        tar -xzf ${CUR_PATH}/3rd/redis.tar.gz -C ${AGENT_LIB} --overwrite > /dev/null
    fi
    [ -d ${AGENT_LIB}/redisdata ] || mkdir -p ${AGENT_LIB}/redisdata
    cp -f ${CUR_PATH}/bin/conf/redis.conf ${AGENT_LIB}/redisdata/ 2>/dev/null || true
    chmod 600 "${AGENT_LIB}/redisdata/redis.conf" 2>/dev/null || true

    cat << EOF > /usr/lib/systemd/system/redis.service
[Unit]
Description=Redis
After=syslog.target

[Service]
User=redis
Group=redis
ExecStart=${AGENT_LIB}/redis-server ${AGENT_LIB}/redisdata/redis.conf
RestartSec=5s
Restart=on-success

[Install]
WantedBy=multi-user.target
EOF

    chown redis:redis -R ${AGENT_LIB}
    systemctl daemon-reload
    systemctl restart redis
    systemctl enable redis
}

create_watchdog_service() {
    cp -f ${CUR_PATH}/scripts/agent_watching.sh ${AGENT_BIN}/agent_watching.sh 2>/dev/null || true
    chmod +x ${AGENT_BIN}/agent_watching.sh

    cat << EOF > /usr/lib/systemd/system/monitor_agentwatching.service
[Unit]
Description=Watching Monitor Agent process

[Service]
Type=simple
ExecStart=${AGENT_BIN}/agent_watching.sh
EOF

    cat << EOF > /usr/lib/systemd/system/monitor_agentwatching.timer
[Unit]
Description=Watching process every 5 seconds

[Timer]
OnBootSec=120
OnUnitActiveSec=5
AccuracySec=1ms
Unit=monitor_agentwatching.service

[Install]
WantedBy=timers.target
EOF
}

_gen_logrotate() {
    cat << EOF > /etc/logrotate.d/monitor_agent
${LOG_PATH}/*.log {
    daily
    rotate 10
    size 20M
    missingok
    notifempty
    sharedscripts
    delaycompress
    copytruncate
}
EOF
}

# 参数解析
while [[ $# -gt 0 ]]; do
    case $1 in
        -i|--ip)
            BIND_IP="$2"
            shift 2
            ;;
        -o|--offline)
            OFFLINE=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Invalid argument: $1"
            usage
            ;;
    esac
done

# OS 检查
if grep -qi centos /etc/os-release; then
    OS_TYPE="centos"
elif grep -qi ubuntu /etc/os-release; then
    OS_TYPE="ubuntu"
else
    echo "Unsupported OS, only support Ubuntu & CentOS."
    exit 1
fi

disable_selinux
env_check

# 复制源码、配置和脚本
cp -rf ${CUR_PATH}/app ${AGENT_ROOT}/ 2>/dev/null || true
cp -f ${CUR_PATH}/conf/* ${AGENT_ROOT}/conf/ 2>/dev/null || true
cp -rf ${CUR_PATH}/scripts/* ${AGENT_ROOT}/scripts/ 2>/dev/null || true
chmod +x ${AGENT_ROOT}/scripts/*.sh 2>/dev/null || true

install_python_deps
create_start_script
create_main_service
redis_check
create_watchdog_service

# Agent 自身目录/文件归 fsgeeker；Redis 的 lib 目录保持 redis:redis
chown -R ${AGENT_USER}:${AGENT_GROUP} ${AGENT_ROOT}
chown -R ${AGENT_USER}:${AGENT_GROUP} ${LOG_PATH}
chown -R redis:redis ${AGENT_LIB}

_gen_logrotate

systemctl daemon-reload
systemctl enable --now monitor_agent.service
systemctl enable --now monitor_agentwatching.timer

echo "=== Monitor Agent installed successfully! ==="
echo "Bind IP: ${BIND_IP}"
echo "Install mode: $([ "${OFFLINE}" -eq 1 ] && echo offline || echo online)"
systemctl status monitor_agent.service --no-pager
echo "Redis status:"
systemctl status redis --no-pager
echo "Watchdog timer status:"
systemctl status monitor_agentwatching.timer --no-pager