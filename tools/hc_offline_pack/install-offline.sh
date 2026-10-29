#!/bin/sh
# ============================================================
# hcxdumptool 离线安装器（opkg / apk 双栈）
#
# 用法（把整个 hc_offline_pack 目录拷到 /root/ 下）：
#   sh install-offline.sh              # 安装（自动选版本/架构）
#   sh install-offline.sh --dry-run    # 只演算，不落盘
#   sh install-offline.sh --verify     # 只校验包完整性（SHA256）
#   sh install-offline.sh --list       # 列出版本/架构矩阵
#   sh install-offline.sh --smoke      # 装完再做"真能抓包"验证（逐个 phy 试，8 秒/个）
#   sh install-offline.sh --smoke=phy0 # 指定 phy / 已存在的监听口做验证
#   sh install-offline.sh --force      # 只覆盖 hcxdumptool 本体（换方言/降级/修复）
#   sh install-offline.sh --force-all  # 连依赖库一起覆盖（会升级/降级 libpcap、libopenssl，慎用）
#   sh install-offline.sh -b 24.10/mipsel_24kc   # 指定目录（跨版本装，谨慎）
#
# 退出码: 0 成功 | 2 完整性失败 | 3 安装失败 | 4 装完找不到可执行 | 5 真跑验证失败
# ============================================================

HERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
RELEASE_FILE="${OWRT_RELEASE_FILE:-/etc/openwrt_release}"
SUPPORTED_BRANCH="22.03 23.05 24.10 25.12"
SUPPORTED_ARCH="mipsel_24kc aarch64_cortex-a53"

usage() { sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

list_matrix() {
    echo "本包集包含的组合（<分支>/<架构>）："
    for d in "$HERE"/*/*; do
        [ -d "$d/pkgs" ] || continue
        printf '  %-8s %-22s %s 个包\n' "$(basename "$(dirname "$d")")" "$(basename "$d")" \
            "$(ls "$d/pkgs" 2>/dev/null | wc -l)"
    done
}

KNOWN() { case " $1 " in *" $2 "*) return 0 ;; esac; return 1; }

ACTION=install
FORCE_BUNDLE=""
FORCE=0
FORCE_ALL=0
PRIMARY=hcxdumptool
SMOKE=""
SMOKE_ARG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n) ACTION=dry ;;
        --verify)     ACTION=verify ;;
        --list|-l)    ACTION=list ;;
        --force|-f)   FORCE=1 ;;
        --force-all|-F) FORCE_ALL=1; FORCE=1 ;;
        --smoke)      SMOKE=1 ;;
        --smoke=*)    SMOKE=1; SMOKE_ARG="${1#--smoke=}" ;;
        -b|--bundle)  shift; FORCE_BUNDLE="$1" ;;
        --bundle=*)   FORCE_BUNDLE="${1#--bundle=}" ;;
        -h|--help)    usage ;;
        *) echo "未知参数: $1（--help 看用法）"; exit 1 ;;
    esac
    shift
done

[ "$ACTION" = list ] && { list_matrix; exit 0; }
[ "$FORCE_ALL" = 1 ] && echo "警告: --force-all 会把依赖库也一起覆盖（可能升级/降级 libpcap、libopenssl），只在清楚后果时用"

# ---------- 1. 识别系统 ----------
if [ ! -f "$RELEASE_FILE" ]; then
    echo "错误: 找不到 $RELEASE_FILE —— 本安装器只支持 OpenWrt（原版或第三方编译版都算）"
    exit 1
fi
# shellcheck disable=SC1090
. "$RELEASE_FILE"
REL="${DISTRIB_RELEASE:-unknown}"
ARCH="${DISTRIB_ARCH:-}"

BRANCH=$(echo "$REL" | sed -n 's/^\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')
if [ -z "$ARCH" ]; then
    case "$(uname -m)" in
        mips*)   ARCH=mipsel_24kc ;;
        aarch64) ARCH=aarch64_cortex-a53 ;;
        arm*)    ARCH=arm_cortex-a7_neon-vfpv4 ;;
        x86_64)  ARCH=x86_64 ;;
        *)       ARCH="$(uname -m)" ;;
    esac
    echo "提示: /etc/openwrt_release 里没有 DISTRIB_ARCH，按 uname -m 推测为 $ARCH"
fi

echo "系统: OpenWrt $REL   架构: $ARCH   分支: ${BRANCH:-未知}"

# ---------- 2. 选目录 + 版本/架构检查 ----------
if [ -n "$FORCE_BUNDLE" ]; then
    case "$FORCE_BUNDLE" in
        /*) BUNDLE="$FORCE_BUNDLE" ;;
        *)  BUNDLE="$HERE/$FORCE_BUNDLE" ;;
    esac
    echo "指定目录: $BUNDLE（已跳过版本/架构校验，只在确定 ABI 兼容时这么用）"
else
    if [ -z "$BRANCH" ]; then
        echo "错误: 从 DISTRIB_RELEASE='$REL' 解析不出版本分支（SNAPSHOT / 自编译版本号都属这种情况）。"
        echo "      本包集只覆盖正式版分支: $SUPPORTED_BRANCH"
        echo "      可选: ① 用 -b 显式指定目录（如 -b 25.12/aarch64_cortex-a53，需自行确认库版本对得上）"
        list_matrix
        exit 1
    fi
    if ! KNOWN "$SUPPORTED_BRANCH" "$BRANCH"; then
        echo "错误: 固件分支 $BRANCH 没有打包。"
        echo "      本包集覆盖的分支: $SUPPORTED_BRANCH（各取该分支最后一个正式版）"
        echo "      补打办法: 改 build_bundles.py 里的 RELS 加上你的分支，重跑 python build_bundles.py"
        list_matrix
        exit 1
    fi
    if ! KNOWN "$SUPPORTED_ARCH" "$ARCH"; then
        echo "错误: 架构 $ARCH 没有打包。"
        echo "      本包集只做了: $SUPPORTED_ARCH"
        echo "      （mipsel_24kc = ramips/mt7621 等；aarch64_cortex-a53 = mediatek/filogic 等）"
        echo "      补打办法: 在 build_bundles.py 的 ARCHES 里加上 '$ARCH'，重跑脚本（每分支约 1–2MB）"
        echo "      手头这台设备的 hcxdumptool 建议从官方 feed 直接取:"
        echo "        https://downloads.openwrt.org/releases/<版本>/packages/$ARCH/packages/"
        exit 1
    fi
    BUNDLE="$HERE/$BRANCH/$ARCH"
fi

if [ ! -d "$BUNDLE/pkgs" ]; then
    echo "错误: 找不到 $BUNDLE/pkgs —— 这份包集不完整或没拷全。"
    echo "      该分支已打的架构: $(ls "$HERE/${BRANCH:-?}" 2>/dev/null | tr '\n' ' ')"
    echo "      该架构已打的分支: $(for b in $SUPPORTED_BRANCH; do [ -d "$HERE/$b/$ARCH/pkgs" ] && printf '%s ' "$b"; done)"
    list_matrix
    exit 1
fi
PKGS="$BUNDLE/pkgs"

# ---------- 3. 包格式 / 包管理器 ----------
MODE=""
ls "$PKGS"/*.ipk >/dev/null 2>&1 && MODE=opkg
ls "$PKGS"/*.apk >/dev/null 2>&1 && MODE=apk
[ -n "$MODE" ] || { echo "错误: $PKGS 里既没有 .ipk 也没有 .apk"; exit 1; }

if [ "$MODE" = opkg ] && ! command -v opkg >/dev/null 2>&1; then
    echo "错误: 这份是 opkg(.ipk) 包，但本机没有 opkg（25.12 及以后换成了 apk，请用 25.12 那份）"
    exit 1
fi
if [ "$MODE" = apk ] && ! command -v apk >/dev/null 2>&1; then
    echo "错误: 这份是 apk(.apk) 包，但本机没有 apk（24.10 及以前是 opkg，请用对应分支那份）"
    exit 1
fi
echo "包格式: $MODE   目录: $BUNDLE"

# ipk 文件名带架构，先做一次"包与机器架构是否对得上"的粗检，避免装出一堆 exec format error
if [ "$MODE" = opkg ]; then
    _wrong=$(ls "$PKGS"/*.ipk 2>/dev/null | grep -vc "_$ARCH\.ipk$")
    if [ "${_wrong:-0}" -gt 0 ]; then
        echo "错误: 包目录里有 $_wrong 个包的架构不是 $ARCH，拒绝安装（拷错目录了？）"
        ls "$PKGS"/*.ipk | grep -v "_$ARCH\.ipk$" | head -5 | sed 's/^/  /'
        exit 1
    fi
fi

# ---------- 4. 完整性校验 ----------
if [ -f "$BUNDLE/SHA256SUMS.txt" ]; then
    _bad=0; _n=0
    while read -r _sum _f; do
        [ -n "$_f" ] || continue
        _f=$(printf '%s' "$_f" | tr -d '\r')
        _n=$((_n + 1))
        if [ ! -f "$PKGS/$_f" ]; then echo "  缺少文件: $_f"; _bad=1; continue; fi
        _got=$(sha256sum "$PKGS/$_f" 2>/dev/null | awk '{print $1}')
        [ "$_got" = "$_sum" ] || { echo "  SHA256 不符: $_f"; _bad=1; }
    done < "$BUNDLE/SHA256SUMS.txt"
    [ "$_bad" = 0 ] && echo "完整性: 通过（$_n 个文件）" || { echo "完整性: 失败，请重新拷贝包集"; exit 2; }
else
    echo "提示: 没有 SHA256SUMS.txt，跳过完整性校验"
fi
[ "$ACTION" = verify ] && exit 0

# ---------- 5. 安装 ----------
ORDER="$BUNDLE/install.order"
[ -f "$ORDER" ] || { echo "错误: 缺少 install.order"; exit 1; }

is_installed() {
    if [ "$MODE" = opkg ]; then
        opkg status "$1" 2>/dev/null | grep -q 'Status:.* installed'
    else
        apk info -e "$1" >/dev/null 2>&1
    fi
}

installed_version() {
    if [ "$MODE" = opkg ]; then
        opkg status "$1" 2>/dev/null | sed -n 's/^Version: //p' | head -1
    else
        apk list -I "$1" 2>/dev/null | awk '{print $1}' | sed "s/^$1-//" | head -1
    fi
}

FAILED=0; N_INST=0; N_SKIP=0
while read -r _name _file _ver; do
    [ -n "$_name" ] || continue
    _name=$(printf '%s' "$_name" | tr -d '\r')
    _file=$(printf '%s' "$_file" | tr -d '\r')
    _ver=$(printf '%s' "$_ver" | tr -d '\r')
    case "$_name" in \#*) continue ;; esac

    _f=0; _iv=""
    if is_installed "$_name"; then
        _iv=$(installed_version "$_name")
        [ "$FORCE_ALL" = 1 ] && _f=1
        [ "$FORCE" = 1 ] && [ "$_name" = "$PRIMARY" ] && _f=1
        if [ "$_f" != 1 ]; then
            if [ -n "$_iv" ] && [ "$_iv" != "$_ver" ]; then
                echo "  跳过 $_name（已装 $_iv，包内 $_ver —— 换版本用 --force，它只换 hcxdumptool 本体）"
            else
                echo "  跳过 $_name（已安装 ${_iv:-?}）"
            fi
            N_SKIP=$((N_SKIP + 1)); continue
        fi
        echo "  --force: 用 $_ver 替换 $_name（当前 $_iv）"
    fi

    if [ "$ACTION" = dry ]; then
        echo "  [dry] $MODE install $_file"
        if [ "$MODE" = opkg ]; then
            opkg install --noaction "$PKGS/$_file" || FAILED=1
        else
            apk add --simulate --allow-untrusted "$PKGS/$_file" 2>&1 | grep -v 'WARNING: opening from cache' || FAILED=1
        fi
        continue
    fi

    echo "  安装 $_name $_ver"
    if [ "$MODE" = opkg ]; then
        if [ "$_f" = 1 ]; then
            opkg install --force-downgrade "$PKGS/$_file" || FAILED=1
        else
            opkg install "$PKGS/$_file" || FAILED=1
        fi
    else
        apk add --allow-untrusted "$PKGS/$_file" || FAILED=1
    fi

    # 装完回查：命令返回 0 也可能是 "Not downgrading" 这种空转，必须核对版本
    _after=$(installed_version "$_name")
    if [ -z "$_after" ]; then
        echo "  !! $_name 安装后没有登记到包数据库"; FAILED=1
    elif [ "$_after" != "$_ver" ]; then
        echo "  !! $_name 版本仍是 $_after，期望 $_ver（opkg 拒绝降级？先 opkg remove $_name 再装）"
        FAILED=1
    else
        N_INST=$((N_INST + 1))
    fi
done < "$ORDER"

echo
if [ "$FAILED" != 0 ]; then
    echo "安装失败（新装 $N_INST，跳过 $N_SKIP）。上面的输出里有失败项，退出码 3。"
    exit 3
fi
if [ "$ACTION" = dry ]; then
    echo "演算完成（未做任何修改）：将新装 $N_INST 个，跳过 $N_SKIP 个。"
    exit 0
fi
echo "安装完成：新装 $N_INST 个，跳过已装 $N_SKIP 个。"
# ---------- 6. 装完检查：存在 / 能执行 / 依赖齐 / 方言 ----------
HCX=$(command -v hcxdumptool 2>/dev/null)
[ -n "$HCX" ] || HCX=/usr/sbin/hcxdumptool
if [ ! -x "$HCX" ]; then
    echo "错误: 装完仍找不到 hcxdumptool 可执行文件（退出码 4）"; exit 4
fi
echo "可执行: $HCX"

_ver=$("$HCX" --version 2>&1 | head -1)
if [ -z "$_ver" ]; then
    echo "错误: $HCX --version 没有任何输出 —— 二进制起不来（库缺失或架构不对）"; exit 4
fi
echo "版本:   $_ver"

if command -v ldd >/dev/null 2>&1; then
    _miss=$(ldd "$HCX" 2>/dev/null | grep 'not found')
    if [ -n "$_miss" ]; then
        echo "错误: 缺少动态库，装上的 hcxdumptool 无法运行："
        echo "$_miss" | sed 's/^/  /'
        echo "      → 说明这份包的依赖和你系统 ABI 不匹配，别用这份，换同分支的包集"
        exit 4
    fi
    echo "依赖库: ldd 检查通过（无 not found）"
else
    echo "依赖库: 无 ldd，已由 --version 成功执行间接验证"
fi

_w=$("$HCX" -h 2>&1 | grep -c '^-w ')
_o=$("$HCX" -h 2>&1 | grep -c '^-o ')
if [ "$_w" -gt 0 ]; then DIALECT=new; else DIALECT=legacy; fi
echo "方言:   -w=$_w -o=$_o → $DIALECT（hcxdumptool_v2609.sh 会自动判，两种都能跑）"

# ---------- 7. 真跑验证（可选，--smoke） ----------
# 注意: hcxdumptool 一启动会扫描全部 phy，只给其中一个建监听口会让它报
# "driver is broken"/"PACKET_STATISTICS failed"（在 10.0.0.1 上实测），
# 所以这里给所有 phy 都建临时监听口，跑完统一删除。
# mac80211 的 monitor 口不能用 ip link del（实测报 "Not supported"），必须走 iw
del_iface() { iw dev "$1" del 2>/dev/null || ip link del "$1" 2>/dev/null; }

smoke_shot() {
    # $1=接口 $2=输出 $3=日志
    rm -f "$2" "$3"
    if [ "$DIALECT" = legacy ]; then
        "$HCX" -i "$1" -o "$2" -c 1,6,11 >"$3" 2>&1 &
    else
        "$HCX" -i "$1" -w "$2" -F >"$3" 2>&1 &
    fi
    _p=$!
    sleep 8
    kill -TERM "$_p" 2>/dev/null
    wait "$_p" 2>/dev/null
    [ -f "$2" ] && [ "$(wc -c < "$2")" -gt 0 ]
}

smoke_test() {
    _want=$1
    _phys=$(ls /sys/class/ieee80211 2>/dev/null)
    if [ -z "$_phys" ]; then
        echo "  真跑验证: /sys/class/ieee80211 为空，没有可用射频"
        return 1
    fi
    _made=""; _idx=0
    for _p in $_phys; do
        _if="hcxsmk$_idx"
        del_iface "$_if"
        iw phy "$_p" interface add "$_if" type monitor 2>/dev/null || \
            ip link add "$_if" type monitor 2>/dev/null
        if [ -d "/sys/class/net/$_if" ]; then
            ip link set "$_if" up 2>/dev/null
            _made="$_made $_if"
        fi
        _idx=$((_idx + 1))
    done
    if [ -z "$_made" ]; then
        echo "  真跑验证: 一个监听口都建不出来（驱动不支持 monitor / 射频没加载）"
        return 1
    fi

    # 候选: 用户指定 > 单接口 / phyN 映射 > 全部逐个试
    case "$_want" in
        "")    _cands=$_made ;;
        phy*)  _pos=$(printf '%s\n' $_phys | grep -n "^$_want\$" | cut -d: -f1)
               [ -n "$_pos" ] && _cands="hcxsmk$((_pos - 1))" || _cands="" ;;
        *)     _cands=$_want ;;
    esac
    if [ -z "$_cands" ]; then
        echo "  真跑验证: 指定的 $_want 对不上任何射频"
        for _if in $_made; do del_iface "$_if"; done
        return 1
    fi

    _ok=0
    for _if in $_cands; do
        echo "  跑 $_if（8 秒）..."
        if smoke_shot "$_if" "/tmp/.hcxsmoke_$$.pcapng" "/tmp/.hcxsmoke_$$.log"; then
            echo "  真跑验证: 通过 —— $_if 抓到 $(wc -c < "/tmp/.hcxsmoke_$$.pcapng") 字节"
            _ok=1
            break
        fi
        echo "    没抓到包，工具输出末尾："
        tail -6 "/tmp/.hcxsmoke_$$.log" 2>/dev/null | sed 's/^/      /'
    done
    rm -f "/tmp/.hcxsmoke_$$.pcapng" "/tmp/.hcxsmoke_$$.log"
    for _if in $_made; do del_iface "$_if"; done
    [ "$_ok" = 1 ]
}

if [ "$SMOKE" = 1 ] && [ "$ACTION" != dry ]; then
    echo
    echo "真跑验证（每个候选射频 8 秒，临时监听口结束自动删）"
    if ! command -v iw >/dev/null 2>&1; then
        echo "  跳过: 本机没有 iw（建监听口需要它）"
        exit 0
    fi
    if smoke_test "$SMOKE_ARG"; then
        echo "结果: 这个 hcxdumptool 在本机能真抓包"
    else
        echo "结果: 真跑验证失败（退出码 5）"
        echo "  常见原因: ① 射频被 AP 占着 → 先停用对应 wifi；② 驱动不支持 monitor；③ 没有 cfg80211/mac80211"
        exit 5
    fi
fi
exit 0