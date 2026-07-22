#!/bin/bash
log_file="/var/log/monitor_agent/watching.log"
AGENT_NFS=(monitor_agent)

log_watching() {
    echo "`date +%y/%m/%d_%H:%M:%S`:: $*" >> ${log_file}
}

check_status() {
    for nf in "${AGENT_NFS[@]}"; do
        systemctl -q is-active ${nf}
        if [ $? -ne 0 ]; then
            log_watching "${nf} is not running."
            systemctl start "${nf}"
        fi
    done
}

check_status
