#!/bin/bash
# 在已在线安装、可正常运行的机器上执行。
# x86_64 出 monitor_agent_offline_x86.tar.gz
# aarch64/arm64 出 monitor_agent_offline_arm.tar.gz
#
# 用法：
#   ./make_package.sh
#   ./make_package.sh -s /path/to/source
#   ./make_package.sh -v /opt/monitor_agent/venv
#   ./make_package.sh -o /tmp

set -euo pipefail

CUR_PATH=$(dirname "$(readlink -f "$0")")
SRC_DIR="${CUR_PATH}"
VENV_PATH="/opt/monitor_agent/venv"
OUT_DIR="${CUR_PATH}"
PKG_DIR_NAME="monitor_agent_offline"

die() {
    echo "[ERROR] $*" >&2
    exit 1
}

log() {
    echo "[$(date '+%F %T')] $*"
}

usage() {
    cat << EOF
Usage: $(basename "$0") [-s SRC_DIR] [-v VENV_PATH] [-o OUT_DIR]
  -s  源码目录，需含 app/ requirements.txt install.sh，默认脚本所在目录
  -v  已装好的 venv，默认 /opt/monitor_agent/venv
  -o  输出目录，默认脚本所在目录
  -h  帮助
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--src)
            SRC_DIR="$2"
            shift 2
            ;;
        -v|--venv)
            VENV_PATH="$2"
            shift 2
            ;;
        -o|--out)
            OUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            die "未知参数: $1"
            ;;
    esac
done

ARCH=$(uname -m)

case "${ARCH}" in
    x86_64)
        ARCH_TAG="x86"
        ;;
    aarch64|arm64)
        ARCH_TAG="arm"
        ;;
    *)
        die "不支持的架构: ${ARCH}，只支持 x86_64 / aarch64"
        ;;
esac

[ -d "${SRC_DIR}/app" ] \
    || die "源码目录缺少 app/: ${SRC_DIR}"

[ -f "${SRC_DIR}/requirements.txt" ] \
    || die "源码目录缺少 requirements.txt"

[ -f "${SRC_DIR}/install.sh" ] \
    || die "源码目录缺少 install.sh"

[ -x "${VENV_PATH}/bin/pip" ] \
    || die "找不到 venv pip: ${VENV_PATH}/bin/pip"

[ -x "${VENV_PATH}/bin/python" ] \
    || die "找不到 venv python: ${VENV_PATH}/bin/python"

PY_VER=$(
    "${VENV_PATH}/bin/python" \
        -c 'import sys; print("%d.%d" % sys.version_info[:2])'
)

log "架构=${ARCH} (${ARCH_TAG})  Python=${PY_VER}"
log "源码=${SRC_DIR}"
log "venv=${VENV_PATH}"

WORK=$(mktemp -d /tmp/monitor_agent_pkg.XXXXXX)
STAGE="${WORK}/${PKG_DIR_NAME}"

mkdir -p \
    "${STAGE}/wheels" \
    "${STAGE}/apt_debs" \
    "${STAGE}/3rd" \
    "${STAGE}/bin" \
    "${STAGE}/lib" \
    "${STAGE}/conf" \
    "${STAGE}/scripts"

trap 'rm -rf "${WORK}"' EXIT

log "复制 app / conf / scripts / bin / 3rd / lib"

cp -a "${SRC_DIR}/app" "${STAGE}/"

[ -d "${SRC_DIR}/conf" ] \
    && cp -a "${SRC_DIR}/conf/." "${STAGE}/conf/" \
    || true

[ -d "${SRC_DIR}/scripts" ] \
    && cp -a "${SRC_DIR}/scripts/." "${STAGE}/scripts/" \
    || true

[ -d "${SRC_DIR}/bin" ] \
    && cp -a "${SRC_DIR}/bin/." "${STAGE}/bin/" \
    || true

[ -d "${SRC_DIR}/3rd" ] \
    && cp -a "${SRC_DIR}/3rd/." "${STAGE}/3rd/" \
    || true

[ -d "${SRC_DIR}/lib" ] \
    && cp -a "${SRC_DIR}/lib/." "${STAGE}/lib/" \
    || true

cp -f \
    "${SRC_DIR}/requirements.txt" \
    "${STAGE}/requirements.txt"

# 现在只有一个 install.sh，直接复制，不做任何修改
cp -f \
    "${SRC_DIR}/install.sh" \
    "${STAGE}/install.sh"

chmod +x "${STAGE}/install.sh" 2>/dev/null || true
chmod +x "${STAGE}/scripts/"*.sh 2>/dev/null || true


# ============================================================
# 下载 Python 3.x 系统组件 + venv 离线安装包
# ============================================================

command -v apt >/dev/null 2>&1 \
    || die "打包机缺少 apt 命令"

log "下载当前已安装的 Python ${PY_VER} 系统组件及 venv 离线 deb 包"

(
    cd "${STAGE}/apt_debs"

    # 按打包机当前已安装版本下载，确保 Python 3.x 核心组件与 venv 版本一致。
    # 例如当前环境为 3.10.12-1~22.04.18，则整套 .18 一起进入离线包。
    for pkg in \
        "python${PY_VER}" \
        "python${PY_VER}-minimal" \
        "libpython${PY_VER}" \
        "libpython${PY_VER}-minimal" \
        "libpython${PY_VER}-stdlib" \
        "python${PY_VER}-venv" \
        python3-pip-whl \
        python3-setuptools-whl
    do
        VER=$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null) \
            || die "打包机未安装软件包: $pkg"

        log "下载 $pkg=$VER"

        apt download "${pkg}=${VER}" \
            || die "下载失败: $pkg=$VER"
    done
)

# 确认 python3.x-venv 已经下载
VENV_DEB_COUNT=$(
    find "${STAGE}/apt_debs" \
        -maxdepth 1 \
        -type f \
        -name "python${PY_VER}-venv_*.deb" \
        | wc -l
)

[ "${VENV_DEB_COUNT}" -gt 0 ] \
    || die "未下载到 python${PY_VER}-venv deb 包"

DEB_COUNT=$(
    find "${STAGE}/apt_debs" \
        -maxdepth 1 \
        -type f \
        -name '*.deb' \
        | wc -l
)

log "apt_debs 数量: ${DEB_COUNT}"


# ============================================================
# Python wheels
# ============================================================

log "从当前 venv 导出 requirements.lock.txt"

"${VENV_PATH}/bin/pip" freeze --all \
    | tee "${STAGE}/requirements.lock.txt" \
    >/dev/null

log "下载 pip / setuptools / wheel"

"${VENV_PATH}/bin/pip" download \
    pip \
    setuptools \
    wheel \
    -d "${STAGE}/wheels"

log "按 lock 下载全部依赖 wheel"

"${VENV_PATH}/bin/pip" download \
    -r "${STAGE}/requirements.lock.txt" \
    -d "${STAGE}/wheels"

WHEEL_COUNT=$(
    find "${STAGE}/wheels" \
        -type f \
        \( -name '*.whl' -o -name '*.tar.gz' \) \
        | wc -l
)

[ "${WHEEL_COUNT}" -gt 0 ] \
    || die "wheels 目录为空"

log "wheels 数量: ${WHEEL_COUNT}"

for pkg in icmplib fastapi numpy pandas uvicorn pip; do
    if ! ls "${STAGE}/wheels" | grep -qi "${pkg}"; then
        echo "[WARN] wheels 里没看到 ${pkg}"
    fi
done

SRC_PKGS=$(
    find "${STAGE}/wheels" \
        -name '*.tar.gz' \
        | wc -l
)

if [ "${SRC_PKGS}" -gt 0 ]; then
    echo "[WARN] wheels 里有 ${SRC_PKGS} 个 .tar.gz 源码包，现场若无编译器可能装失败："

    find "${STAGE}/wheels" \
        -name '*.tar.gz' \
        -printf '  %f\n'
fi


# ============================================================
# PACKAGE INFO
# ============================================================

cat > "${STAGE}/PACKAGE_INFO.txt" << EOF
arch=${ARCH}
arch_tag=${ARCH_TAG}
python=${PY_VER}
built_at=$(date '+%F %T')
built_host=$(hostname)
venv=${VENV_PATH}
python_venv_deb=python${PY_VER}-venv
EOF


# ============================================================
# 打包
# ============================================================

mkdir -p "${OUT_DIR}"

OUT_FILE="${OUT_DIR}/${PKG_DIR_NAME}_${ARCH_TAG}.tar.gz"

log "打包 ${OUT_FILE}"

tar \
    -C "${WORK}" \
    -czf "${OUT_FILE}" \
    "${PKG_DIR_NAME}"

log "完成"

ls -lh "${OUT_FILE}"

echo
echo "现场安装："
echo "  tar -zxf ${PKG_DIR_NAME}_${ARCH_TAG}.tar.gz -C /tmp"
echo "  cd /tmp/${PKG_DIR_NAME}"
echo "  ./install.sh -o -i 0.0.0.0"
echo
echo "离线包已包含："
echo "  wheels/     Python 第三方依赖"
echo "  apt_debs/   Python ${PY_VER} 系统组件 + venv 离线 deb"
echo
echo "现场需已有 Python ${PY_VER}，且 uname -m 为 ${ARCH}。"