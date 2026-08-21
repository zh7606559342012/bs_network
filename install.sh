#!/bin/bash
CUR_PATH=$(dirname "$(readlink -f "$0")")
AGENT_ROOT="/opt/monitor_agent"
AGENT_BIN="${AGENT_ROOT}/bin"
VENV_PATH="${AGENT_ROOT}/venv"
AGENT_LIB="${AGENT_ROOT}/lib"
LOG_PATH="/var/log/monitor_agent"
BIND_IP="0.0.0.0"

die() { echo "$1"; exit 1; }

usage() {
    script_name=$(basename "$0")
    echo "./${script_name} [-i 0.0.0.0]  # bind local ip addr"
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
    rm -rf ${AGENT_ROOT} >/dev/null 2>&1
    mkdir -p ${AGENT_ROOT} ${AGENT_BIN} ${VENV_PATH} ${LOG_PATH} ${AGENT_LIB} ${AGENT_ROOT}/conf ${AGENT_ROOT}/scripts
}

install_python_deps() {
    python3 -m venv ${VENV_PATH}
    ${VENV_PATH}/bin/pip install --upgrade pip
    ${VENV_PATH}/bin/pip install -r ${CUR_PATH}/requirements.txt
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
User=root
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
        -h|--help)
            usage
            ;;
        *)
            echo "Invalid argument"
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
cp -f ${CUR_PATH}/bin/conf/* ${AGENT_ROOT}/conf/ 2>/dev/null || true
cp -rf ${CUR_PATH}/scripts/* ${AGENT_ROOT}/scripts/ 2>/dev/null || true
chmod +x ${AGENT_ROOT}/scripts/*.sh 2>/dev/null || true

install_python_deps
create_start_script
create_main_service
redis_check
create_watchdog_service
_gen_logrotate

systemctl daemon-reload
systemctl enable --now monitor_agent.service
systemctl enable --now monitor_agentwatching.timer

echo "=== Monitor Agent installed successfully! ==="
echo "Bind IP: ${BIND_IP}"
systemctl status monitor_agent.service --no-pager
echo "Redis status:"
systemctl status redis --no-pager
echo "Watchdog timer status:"
systemctl status monitor_agentwatching.timer --no-pager