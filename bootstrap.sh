#!/usr/bin/env bash
# 谐振器测试数据平台 - 一键启动
# 用法：./bootstrap.sh [up|down|reset|status]
#   up      启动全部 5 容器（默认）
#   down    停止全部容器（保留数据）
#   reset   销毁容器 + 删除 /data3/aln 业务数据（需二次确认）
#   status  查看容器状态 + /api/health + 磁盘

set -euo pipefail

# ---------- 路径 / 常量 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/deploy/docker-compose.yml"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
# DATA_ROOT 从 .env 读，无 .env 时回落到默认；保证脚本与 docker-compose 看到同一份值
DATA_ROOT_DEFAULT="/data3/aln"
DATA_ROOT=""  # 由 load_data_root 在校验完 .env 后填充
DATA_SUBDIRS=(pgdata redis uploads files mappings exports logs/api logs/worker)
FRONTEND_DIST="${SCRIPT_DIR}/frontend/dist"

NGINX_PORT_DEFAULT="18080"
HEALTH_TIMEOUT_SECS=60

# ---------- 颜色 ----------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RED=$'\033[31m'
    C_GRN=$'\033[32m'
    C_YEL=$'\033[33m'
    C_RST=$'\033[0m'
else
    C_RED=""
    C_GRN=""
    C_YEL=""
    C_RST=""
fi

log_info()  { printf '%s[INFO]%s  %s\n'  "${C_YEL}" "${C_RST}" "$*"; }
log_ok()    { printf '%s[OK]%s    %s\n'  "${C_GRN}" "${C_RST}" "$*"; }
log_error() { printf '%s[ERROR]%s %s\n'  "${C_RED}" "${C_RST}" "$*" >&2; }

die() {
    log_error "$*"
    exit 1
}

# 剥掉 .env 值的成对包裹引号 — 用户经常写 DATA_ROOT="/data3/aln"，
# 直接 cut -d= -f2- 会把引号也带进来，让 mkdir 创建字面 "data3..." 目录。
strip_quotes() {
    local v="$1"
    [[ "${v}" == \"*\" && "${v: -1}" == '"' ]] && v="${v:1:-1}"
    [[ "${v}" == \'*\' && "${v: -1}" == "'" ]] && v="${v:1:-1}"
    printf '%s' "${v}"
}

# ---------- 通用工具 ----------
get_nginx_port() {
    if [[ -f "${ENV_FILE}" ]]; then
        local v
        v="$(grep -E '^NGINX_PORT=' "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
        v="$(strip_quotes "${v}")"
        [[ -n "${v}" ]] && { echo "${v}"; return; }
    fi
    echo "${NGINX_PORT_DEFAULT}"
}

load_data_root() {
    if [[ -f "${ENV_FILE}" ]]; then
        local v
        v="$(grep -E '^DATA_ROOT=' "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
        v="$(strip_quotes "${v}")"
        if [[ -n "${v}" ]]; then
            DATA_ROOT="${v}"
        else
            DATA_ROOT="${DATA_ROOT_DEFAULT}"
        fi
    else
        DATA_ROOT="${DATA_ROOT_DEFAULT}"
    fi
    # 相对路径自动展开为相对项目根的绝对路径——避免 docker compose 把它
    # 解析到 deploy/ 子目录下。展开后导出给 compose 子进程，覆盖 .env 里的相对值。
    if [[ "${DATA_ROOT}" != /* ]]; then
        DATA_ROOT="${SCRIPT_DIR}/${DATA_ROOT#./}"
        log_info "DATA_ROOT 是相对路径，已展开为 ${DATA_ROOT}"
    fi
    export DATA_ROOT
}

# reset 前的安全闸：拒掉根 / 短路径 / 相对路径，避免 rm -rf 误操作根文件系统。
assert_data_root_safe() {
    [[ -n "${DATA_ROOT}" ]]                   || die "DATA_ROOT 为空，拒绝 reset"
    [[ "${DATA_ROOT}" == /* ]]                || die "DATA_ROOT 必须是绝对路径，拒绝 reset：${DATA_ROOT}"
    [[ "${DATA_ROOT}" != "/" ]]               || die "DATA_ROOT=/ 危险，拒绝 reset"
    [[ "${#DATA_ROOT}" -ge 8 ]]               || die "DATA_ROOT 太短（${DATA_ROOT}），拒绝 reset 防误删"
    [[ "${DATA_ROOT}" != "/home" ]]           || die "DATA_ROOT=/home 危险，拒绝 reset"
    [[ "${DATA_ROOT}" != "/var" ]]            || die "DATA_ROOT=/var 危险，拒绝 reset"
    [[ "${DATA_ROOT}" != "/etc" ]]            || die "DATA_ROOT=/etc 危险，拒绝 reset"
    [[ "${DATA_ROOT}" != "/usr" ]]            || die "DATA_ROOT=/usr 危险，拒绝 reset"
    [[ "${DATA_ROOT}" != "/tmp" ]]            || die "DATA_ROOT=/tmp 危险，拒绝 reset"
    [[ "${DATA_ROOT}" != "${HOME}" ]]         || die "DATA_ROOT=\$HOME 危险，拒绝 reset"
}

# 探测本机对外（局域网）可达 IP。优先默认路由的 src；回落到非 loopback/bridge/link-local 的 IPv4。
# 用于在 up/url 时打印一个"固定的同事可访问 URL"，避免别人查 ifconfig。
detect_lan_ip() {
    local ip
    ip="$(ip -4 -o route show default 2>/dev/null \
        | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"
    if [[ -n "${ip}" ]]; then
        echo "${ip}"
        return
    fi
    ip="$(ip -4 -o addr show 2>/dev/null \
        | awk '$2!="lo" && $2!~/^(docker|podman|cni-|veth|br-)/ {print $4}' \
        | cut -d/ -f1 \
        | grep -vE '^(127\.|169\.254\.)' \
        | head -n1)"
    [[ -n "${ip}" ]] && echo "${ip}"
}

compose() {
    podman compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"
}

# ---------- 前置检查 ----------
check_env_file() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        log_error ".env 不存在"
        log_info  "请先执行：cp ${ENV_EXAMPLE} ${ENV_FILE}  并修改 POSTGRES_PASSWORD"
        exit 1
    fi
    load_data_root
    log_ok ".env 已就位（DATA_ROOT=${DATA_ROOT}）"
}

check_podman() {
    command -v podman >/dev/null 2>&1 || die "未找到 podman，请先安装"
    if ! podman compose version >/dev/null 2>&1; then
        die "podman compose 不可用（podman >= 4.9 自带，或安装 podman-compose）"
    fi
    log_ok "podman + podman compose 可用"
}

ensure_data_dirs() {
    if [[ ! -d "${DATA_ROOT}" ]]; then
        log_info "创建数据根 ${DATA_ROOT}（需要 sudo）"
        sudo mkdir -p "${DATA_ROOT}"
        sudo chown "$(id -u):$(id -g)" "${DATA_ROOT}"
    fi
    for d in "${DATA_SUBDIRS[@]}"; do
        local full="${DATA_ROOT}/${d}"
        if [[ ! -d "${full}" ]]; then
            mkdir -p "${full}" 2>/dev/null || sudo mkdir -p "${full}"
        fi
    done
    # postgres 容器需要 999:999 (alpine 镜像) 写权限——若是新建目录则放宽
    if [[ -d "${DATA_ROOT}/pgdata" && -z "$(ls -A "${DATA_ROOT}/pgdata" 2>/dev/null)" ]]; then
        chmod 777 "${DATA_ROOT}/pgdata" 2>/dev/null || sudo chmod 777 "${DATA_ROOT}/pgdata"
    fi
    log_ok "数据目录就绪：${DATA_ROOT}"
}

# ---------- up ----------
wait_postgres_healthy() {
    log_info "等待 postgres healthy..."
    local i=0
    while (( i < HEALTH_TIMEOUT_SECS )); do
        local status
        status="$(podman inspect -f '{{.State.Health.Status}}' aln-postgres 2>/dev/null || echo "unknown")"
        if [[ "${status}" == "healthy" ]]; then
            log_ok "postgres healthy"
            return 0
        fi
        sleep 2
        i=$((i + 2))
    done
    die "postgres 在 ${HEALTH_TIMEOUT_SECS}s 内未变 healthy"
}

run_migrations() {
    log_info "跑 alembic 迁移..."
    # 优先在 api 容器里跑（保证依赖一致），api 还没起时退回本地 uv
    if podman ps --format '{{.Names}}' | grep -q '^aln-api$'; then
        compose exec -T api alembic upgrade head
    else
        # 临时启动 api 容器只跑迁移（--rm 不保留）
        compose run --rm api alembic upgrade head
    fi
    log_ok "alembic 迁移完成"
}

check_frontend_dist() {
    if [[ ! -d "${FRONTEND_DIST}" ]] || [[ -z "$(ls -A "${FRONTEND_DIST}" 2>/dev/null)" ]]; then
        log_error "frontend/dist 不存在或为空"
        log_info  "请先构建前端："
        log_info  "  cd frontend && npm install && npm run build"
        log_info  "再重新运行 ./bootstrap.sh up"
        exit 1
    fi
    log_ok "frontend/dist 已就绪"
}

verify_health() {
    local port
    port="$(get_nginx_port)"
    local url="http://localhost:${port}/api/health"
    log_info "验证 ${url}..."
    local i=0
    while (( i < HEALTH_TIMEOUT_SECS )); do
        if curl -fsS "${url}" >/dev/null 2>&1; then
            log_ok "API 健康检查通过"
            return 0
        fi
        sleep 2
        i=$((i + 2))
    done
    die "API 在 ${HEALTH_TIMEOUT_SECS}s 内未通过健康检查；用 ./bootstrap.sh status 排查"
}

print_access_info() {
    local port lan_ip
    port="$(get_nginx_port)"
    lan_ip="$(detect_lan_ip)"
    echo
    log_ok "全部启动完毕"
    echo "      本机访问： http://localhost:${port}"
    if [[ -n "${lan_ip}" ]]; then
        echo "      局域网入口：http://${lan_ip}:${port}    ← 同事打开这个"
    else
        echo "      局域网入口：未探测到对外网卡 IP；可用 'ip -4 addr' 自己查"
    fi
    echo "      API 直连： http://localhost:8001"
    echo "      健康检查： http://localhost:${port}/api/health"
    echo
}

cmd_up() {
    check_env_file
    check_podman
    ensure_data_dirs

    log_info "启动 postgres + redis"
    compose up -d postgres redis
    wait_postgres_healthy

    run_migrations

    check_frontend_dist

    log_info "启动 api + worker + nginx"
    compose up -d api worker nginx

    verify_health
    print_access_info
}

# ---------- down ----------
cmd_down() {
    check_env_file
    check_podman
    log_info "停止全部容器"
    compose down
    log_ok "已停止（数据保留在 ${DATA_ROOT}）"
}

# ---------- reset ----------
cmd_reset() {
    check_env_file
    check_podman
    assert_data_root_safe
    cat <<EOF
${C_RED}========================================
  WARNING: 即将销毁容器 + 全部业务数据
  目录：${DATA_ROOT}/{pgdata,redis,uploads,files,exports}
  此操作不可逆。
========================================${C_RST}
EOF
    printf "输入 RESET 继续，其它任何输入取消： "
    read -r confirm
    if [[ "${confirm}" != "RESET" ]]; then
        log_info "已取消"
        exit 0
    fi

    log_info "停止 + 删除容器"
    compose down -v || true

    log_info "删除业务数据"
    for d in pgdata redis uploads files exports; do
        local full="${DATA_ROOT}/${d}"
        if [[ -d "${full}" ]]; then
            rm -rf "${full}" 2>/dev/null || sudo rm -rf "${full}"
            log_ok "已删 ${full}"
        fi
    done
    log_ok "重置完成。下次 ./bootstrap.sh up 将从空数据库开始"
}

# ---------- status ----------
cmd_status() {
    check_env_file
    check_podman

    log_info "容器状态："
    compose ps || true
    echo

    local port
    port="$(get_nginx_port)"
    log_info "API 健康检查 (http://localhost:${port}/api/health)："
    if curl -fsS "http://localhost:${port}/api/health" 2>/dev/null; then
        echo
        log_ok "健康"
    else
        echo
        log_error "不健康或未启动"
    fi
    echo

    log_info "磁盘 (${DATA_ROOT})："
    df -h "${DATA_ROOT}" 2>/dev/null || log_error "${DATA_ROOT} 不存在"
    echo

    local port lan_ip
    port="$(get_nginx_port)"
    lan_ip="$(detect_lan_ip)"
    log_info "访问入口："
    echo "      本机访问： http://localhost:${port}"
    if [[ -n "${lan_ip}" ]]; then
        echo "      局域网入口：http://${lan_ip}:${port}"
    fi
}

# ---------- install-service（systemd --user 开机自启） ----------
SERVICE_NAME="aln-data.service"
SERVICE_FILE="${HOME}/.config/systemd/user/${SERVICE_NAME}"

cmd_install_service() {
    check_env_file
    command -v systemctl >/dev/null 2>&1 || die "未找到 systemctl"
    systemctl --user status >/dev/null 2>&1 \
        || die "systemd --user 不可用（需在已登录 user session 中运行）"

    mkdir -p "$(dirname "${SERVICE_FILE}")"
    log_info "写入 ${SERVICE_FILE}"
    # 注意：HEREDOC 不引用，可让 ${SCRIPT_DIR} 在此处展开为本机绝对路径，
    # 不同机器 clone 到不同位置时各装各的，避免硬编码。
    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=ALN Data Platform (5-container podman stack)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${SCRIPT_DIR}
Environment=NO_COLOR=1
ExecStart=/bin/bash ${SCRIPT_DIR}/bootstrap.sh up
ExecStop=/bin/bash ${SCRIPT_DIR}/bootstrap.sh down
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=default.target
EOF

    log_info "systemctl --user daemon-reload"
    systemctl --user daemon-reload

    log_info "启用并立即启动 ${SERVICE_NAME}"
    systemctl --user enable --now "${SERVICE_NAME}"

    log_ok "已安装 + 启用 ${SERVICE_NAME}"
    if ! loginctl show-user "$(id -un)" 2>/dev/null | grep -q '^Linger=yes'; then
        echo
        log_info "为保证机器重启 / 用户登出后服务仍运行，请执行（需 sudo）："
        echo "      sudo loginctl enable-linger $(id -un)"
        echo
    fi
}

cmd_uninstall_service() {
    if [[ ! -f "${SERVICE_FILE}" ]]; then
        log_info "${SERVICE_FILE} 不存在，无需卸载"
        return
    fi
    log_info "停用 + 删除 ${SERVICE_NAME}"
    systemctl --user disable --now "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "${SERVICE_FILE}"
    systemctl --user daemon-reload
    log_ok "已卸载 ${SERVICE_NAME}（不影响 .env / 容器 / 数据）"
}

# ---------- url（仅打印，不启服务） ----------
cmd_url() {
    check_env_file
    local port lan_ip
    port="$(get_nginx_port)"
    lan_ip="$(detect_lan_ip)"
    echo "本机访问：  http://localhost:${port}"
    if [[ -n "${lan_ip}" ]]; then
        echo "局域网入口：http://${lan_ip}:${port}    ← 告诉同事这个"
    else
        log_error "未探测到对外网卡 IP（无默认路由 / 仅有 link-local），用 'ip -4 addr' 自己查"
    fi
}

# ---------- 入口 ----------
main() {
    local cmd="${1:-up}"
    case "${cmd}" in
        up|start)          cmd_up                ;;
        down|stop)         cmd_down              ;;
        reset)             cmd_reset             ;;
        status)            cmd_status            ;;
        url)               cmd_url               ;;
        install-service)   cmd_install_service   ;;
        uninstall-service) cmd_uninstall_service ;;
        -h|--help|help)
            cat <<EOF
用法：./bootstrap.sh [up|down|reset|status|url|install-service|uninstall-service]

  up | start          启动全部 5 容器（默认）
  down | stop         停止全部容器（保留数据）
  reset               销毁容器 + 删除 \$DATA_ROOT 业务数据（路径由 .env 中的 DATA_ROOT 决定，默认 ${DATA_ROOT_DEFAULT}），需二次确认
  status              查看容器状态 + /api/health + 磁盘 + 访问入口
  url                 仅打印固定访问入口（本机 + 局域网 URL），不影响服务
  install-service     安装 systemd --user 服务，开机自启全栈（之后用 systemctl --user 管理）
  uninstall-service   卸载 systemd 服务（不影响容器和数据）

环境变量：
  NO_COLOR=1   关闭彩色输出
EOF
            ;;
        *)
            log_error "未知子命令：${cmd}"
            log_info  "用 ./bootstrap.sh help 查看用法"
            exit 1
            ;;
    esac
}

main "$@"
