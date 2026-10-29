# hc_offline_pack —— hcxdumptool 离线安装包集

OpenWrt **22.03 / 23.05 / 24.10 / 25.12** 四个版本分支 × **mipsel_24kc / aarch64_cortex-a53** 两种架构，
每份都带齐 hcxdumptool 本体 + 它缺的依赖库，**路由器没网也能装，装完还会验一遍"到底能不能真抓包"**。

- 共 **8 份 / 24 个包 / 13.0 MB**
- 包格式按时代分开：22.03–24.10 是 **opkg(.ipk)**，25.12 是 **apk(.apk)**（apk-tools 3 的 ADB 格式）
- 包全部来自 **OpenWrt 官方 feed**，逐个校验 SHA256

---

## 一、覆盖矩阵（2026-10-20 打包）

| 分支 | 取自正式版 | hcxdumptool | 依赖 | 包格式 | 目录 |
|---|---|---|---|---|---|
| 22.03 | 22.03.7 | **6.2.4-1**（legacy `-o/-c`） | libpcap1 1.10.1-4 + libopenssl1.1 1.1.1w-1 | ipk | `22.03/<arch>` |
| 23.05 | 23.05.6 | **6.2.4-1**（legacy `-o/-c`） | libpcap1 1.10.4-1 + libopenssl3 3.0.16-1 | ipk | `23.05/<arch>` |
| 24.10 | 24.10.8 | **6.3.4-r1**（new `-w/-F`） | libpcap1 1.10.5-r2 + libopenssl3 3.0.22-r1 | ipk | `24.10/<arch>` |
| 25.12 | 25.12.6 | **6.3.4-r1**（new `-w/-F`） | libpcap1 1.10.6-r1 + libopenssl3 3.5.9-r1 | apk | `25.12/<arch>` |

`<arch>` = `mipsel_24kc`（ramips/mt7621，如 R6220、Redmi AC2100）或 `aarch64_cortex-a53`（mediatek/filogic 等）。

**没有 26.x**：截至 2026-10-20 最新正式版是 25.12.6，26 未发布。master 的 hcxdumptool 已到 **7.1.2**
（依赖只剩 libpcap），26 出来后要用 `build_bundles.py` 重打。

## 二、为什么必须按分支分开发

1. **命令行方言不同**：6.2.4 只认 `-o/-c`，6.3.4 只认 `-w/-F`，互相报 `unrecognized option`。
2. **依赖库换代**：22.03 `libopenssl1.1`；23.05/24.10 `libopenssl3`（3.0.x）；25.12 `libopenssl3` 3.5.9。
3. **包管理换代**：24.10 及以前 opkg + `.ipk`；25.12 起 apk-tools 3 + `.apk`（ADB 容器）。
4. **内核差异**：6.2.4 走 WEXT，新内核（无 `/proc/net/wireless`）直接不可用 —— 这正是 `--smoke` 能抓出来的。

## 三、用法

把整个 `hc_offline_pack/` 拷到路由器 `/root/`，然后：

```sh
sh install-offline.sh --list       # 看包集里有哪些版本/架构
sh install-offline.sh --verify     # 只校验完整性（SHA256）
sh install-offline.sh --dry-run    # 只演算，不落盘
sh install-offline.sh              # 安装（自动按固件版本+架构选目录）
sh install-offline.sh --smoke      # 安装 + 真跑验证（建临时监听口真抓 8 秒）
```

| 参数 | 作用 |
|---|---|
| `--dry-run` / `-n` | 只演算（opkg `--noaction`、apk `--simulate`），不写盘 |
| `--verify` | 只校验 SHA256，不安装 |
| `--list` / `-l` | 列出包集矩阵 |
| `--smoke` | 装完做真跑验证（逐个 phy 试，8 秒/个，临时监听口自动删） |
| `--smoke=phy1` / `--smoke=hcxmon0` | 只用指定 phy 或指定已存在的监听口验证 |
| `--force` / `-f` | **只**覆盖 hcxdumptool 本体（换方言、降级、修复时用），依赖库仍只补缺 |
| `--force-all` / `-F` | 连依赖库一起覆盖（会升级/降级 libpcap、libopenssl，危险） |
| `-b <分支>/<架构>` | 指定目录（跨版本装、SNAPSHOT 用），跳过版本/架构校验 |

## 四、安装器到底做了哪些检查（"装成功了吗？能跑吗？"）

**装前**
1. `/etc/openwrt_release` 存在、`DISTRIB_RELEASE` / `DISTRIB_ARCH` 能解析
2. 分支在白名单（22.03/23.05/24.10/25.12）里、架构在白名单（mipsel_24kc/aarch64_cortex-a53）里
3. 包格式与系统包管理器匹配（opkg 系统不会拿 apk 包装；反之亦然）
4. **架构逐包核对**：ipk 文件名里的架构必须等于本机 `DISTRIB_ARCH`，否则拒绝（防"拷错目录"）
5. SHA256 逐个比对（含"文件缺失"明细）

**装中**
6. 依赖顺序安装（库在前、hc 在后），**只补未安装的**，已装的一律不动
7. **装完回查**：从包数据库读回版本，和包内版本不一致就算失败
   —— 实测很有用：opkg 遇到降级会打印 `Not downgrading ...` 却**返回 0**，只看返回码会误判成功

**装后**
8. `hcxdumptool` 可执行文件存在且可执行（`command -v`，兼容 `/usr/bin` 与 `/usr/sbin`）
9. `hcxdumptool --version` 有输出 —— 二进制起得来（缺库/架构错会直接失败）
10. `ldd` 无 `not found`（有 ldd 时），列出缺哪个库，指明是 ABI 不匹配
11. `help` 方言判定（`-w` / `-o`），告诉你会配 `hcxdumptool_v2609.sh` 的哪一支

**可选真跑（`--smoke`）**
12. 在**所有** phy 上建临时 monitor 口（只建一个会让 hcxdumptool 报 `driver is broken`，实测踩过）
13. 真跑 8 秒 `-w … -F`（new）/ `-o … -c 1,6,11`（legacy）
14. 检查产出的 pcapng **字节数 > 0**（0 字节 = 抓不到，判失败）
15. 第一个 phy 不行自动换下一个 phy 再试
16. 跑完删掉临时监听口和临时文件（用 `iw dev … del`；`ip link del` 对 mac80211 monitor 口无效，实测报 `Not supported`）

**退出码**

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 参数/系统识别/找不到包集 等前置错误 |
| 2 | 完整性校验失败（缺文件或 SHA256 不符） |
| 3 | 安装失败（含"装完版本对不上"） |
| 4 | 装完找不到可执行 / 起不来 / 缺库 |
| 5 | `--smoke` 真跑验证失败 |

## 五、架构覆盖：能装在哪些机器上？

**已覆盖 2 种**：`mipsel_24kc`、`aarch64_cortex-a53`。

**OpenWrt 还有一大批别的架构**（本包集**没有**，硬装会被架构检查拦下）：

| 架构 | 典型设备/目标 |
|---|---|
| `arm_cortex-a7_neon-vfpv4` | ipq40xx（部分）、ipq806x、mt7622 部分 |
| `arm_cortex-a9_neon` / `_vfpv3` | bcm53xx（BCM470x）、mvebu（Armada 370/XP） |
| `arm_cortex-a15_neon-vfpv4` | ipq806x（部分） |
| `aarch64_cortex-a72` | bcm27xx（树莓派）、mvebu/cortex-a72、部分 ipq807x |
| `aarch64_generic` | rockchip、sunxi（全志）、armsr（虚拟机） |
| `mips_24kc`（大端） | ath79（很多老 TP-Link/Ubiquiti） |
| `mipsel_24kc_24kf`（硬浮点） | ramips 部分老型号 |
| `mips64_mips64r2` / `mips64el_octeon` | octeon、部分企业路由 |
| `x86_64` / `i386_pentium4` | 软路由、虚拟机 |
| `powerpc_8548`、`loongarch64` 等 | 少数专有机型 |

补打很容易：改 `build_bundles.py` 里的 `ARCHES` 列表加上目标架构，重跑脚本（每分支约 1–2 MB）。
本机真实的 `DISTRIB_ARCH` 直接从 `/etc/openwrt_release` 看。

## 六、没有对应包集时的提示

| 情况 | 提示 |
|---|---|
| 分支不在覆盖内（21.02、19.07…） | `错误: 固件分支 21.02 没有打包` + 覆盖清单 + 补打办法 + 现有矩阵 |
| 解析不出版本（SNAPSHOT、自编译号） | `解析不出版本分支` + 说明 SNAPSHOT 不在覆盖内 + 建议用 `-b` + 现有矩阵 |
| 架构不在覆盖内（arm_cortex-a7…） | `错误: 架构 xxx 没有打包` + 只做了哪两种 + 官方 feed 直取地址 + 现有矩阵 |
| 目录/包不存在 | `找不到 …/pkgs` + **该分支已打哪些架构** + **该架构在哪些分支有** + 现有矩阵 |
| 包与机器架构不符 | `包目录里有 N 个包的架构不是 mipsel_24kc，拒绝安装` + 列出前 5 个不符文件 |
| 格式与系统不符 | `这份是 apk(.apk) 包，但本机没有 apk（25.12 及以后换成了 apk…）` 这类精准提示 |
| 依赖库不匹配 | `ldd` 输出 `not found` 的库名 + "换同分支的包集" |

## 七、哪台机器该用哪份

| 设备固件 | 直接用 | 如果要 6.3.4 |
|---|---|---|
| 22.03.x | `22.03/<arch>` | `-b 24.10/<arch> --force` |
| 23.05.x | `23.05/<arch>` | `-b 24.10/<arch> --force` ← 本项目 10.0.0.1（23.05.0-rc2）就这么装的 |
| 24.10.x | `24.10/<arch>` | 同左 |
| 25.12.x | `25.12/<arch>` | 同左 |
| SNAPSHOT | 不在覆盖内 | `-b 25.12/<arch>`，自行确认库版本对得上 |

⚠️ `22.03`/`23.05` 里的 hc 是 **6.2.4**，在没有 wireless-extensions 的新内核上必然抓不到包
（`failed to detect wlan interface: Not a tty`）—— `--smoke` 会当场把这种"装上了但跑不了"抓出来，退出码 5。

## 八、目录结构

```
hc_offline_pack/
├── README.md                  ← 本文件
├── install-offline.sh         ← 统一安装器（opkg/apk 双栈 + 装后检查 + 可选真跑）
├── build_bundles.py           ← 重新生成包集的脚本（Python 3.8+，阿里云/清华/官方镜像）
├── SHA256SUMS.txt             ← 全部 24 个包的总校验和
├── 22.03/{mipsel_24kc,aarch64_cortex-a53}/
│   ├── pkgs/*.ipk
│   ├── install.order          ← 安装顺序（名字 文件 版本）
│   ├── SHA256SUMS.txt         ← 该份的校验和（LF 换行，busybox 可直接读）
│   ├── deps.txt               ← 人看的依赖清单
│   └── manifest.json          ← 机器看的元数据（feed 来源、索引 SHA256）
├── 23.05/…  24.10/…  25.12/…
```

## 九、真机验证记录（2026-10-20）

| 设备 | 场景 | 结果 |
|---|---|---|
| 13.1（22.03.7 / mipsel / 6.2.4） | `--verify` + `--dry-run` | 完整性通过，三包全跳过，`sh -n` 通过 |
| 13.1 | `--smoke` | **通过**：legacy `-o/-c` 抓到 31772 B，临时口残留 0 |
| 10.1（23.05.0-rc2 / mipsel） | 卸掉 hc 后 `-b 24.10/mipsel_24kc` | 真装成功，6.3.4、new 方言，两个库正确跳过 |
| 10.1 | `--smoke`（6.3.4） | **通过**：10108 / 11540 B，残留 0 |
| 10.1 | 装 23.05 自带 6.2.4 后 `--smoke` | **正确地失败**（`failed to init socket` / `Not a tty`，退出码 5）—— 证明这套检查能识别"装上了但跑不了" |
| 10.1 | `--smoke=nosuchif0` | 失败并打印 `failed to get interface index: No such device`，退出码 5 |
| 10.1 | `--smoke=phy1` | 正确映射到该 phy 的临时口并通过 |
| 10.1 | `--force` 降级 6.3.4→6.2.4→6.3.4 | 只换本体，两个库不动（打印"已装 X，包内 Y"提示） |
| 99.1（SNAPSHOT / aarch64 / apk） | 不带 `-b` | 正确拒绝并提示 SNAPSHOT 不在覆盖内 |
| 99.1 | `-b 25.12/aarch64_cortex-a53 --smoke` | SHA256 通过；hcxsmk0 失败后自动换 hcxsmk1，**通过 25168 B** |
| 10.1（伪造 `/etc/openwrt_release`） | 21.02 / arm_cortex-a7 / SNAPSHOT / 错架构目录 / 缺目录 | 五种提示全部按预期输出，退出码 1 |

未覆盖：25.12 的 **mipsel_24kc** 那份没有真机；`aarch64_cortex-a72` 等其它架构不在包集内。

## 十、重新生成 / 更新

```powershell
python tools\hc_offline_pack\build_bundles.py
```

- 从 `releases/<版本>/packages/<架构>/{base,packages}` 拉索引与包（镜像顺序：阿里云 → 清华 → 官方；
  22.03.7 已 EOL，只有阿里云还有）
- 依赖闭包递归解析 `Depends`；`libc / kernel / libgcc` 这类固件自带项**不打包**
- 25.12 的 apk feed 索引不带依赖信息，脚本里按实测写死 `libpcap1 / libopenssl3`（见 `APK_PKGS` 注释）
- 生成文件一律 **LF 换行**（Windows 下 Python 要显式 `newline="\n"`，否则 busybox 端 `[ -f ]` 会把
  `file.ipk\r` 当不存在，全部误判"缺文件"——踩过）

## 十一、已知限制与注意事项

- 只含 **hcxdumptool + 依赖**，不含 `hcxtools`、aircrack-ng、reaver、tcpdump
- `--force-all` 会真的升级/降级依赖库，**别在别人的生产机上用**
- `--smoke` 会短暂建监听口（8 秒/个 phy）；射频正被 AP 占用时可能抓不到包，先停 wifi 再试
- apk 安装用 `--allow-untrusted`：包是 OpenWrt 官方签名，但离线设备没有对应公钥环
- **实验副作用记录**：验证 `--force` 时（修复前的版本会把依赖也一起覆盖）曾在 10.0.0.1 上把
  `libpcap1` 1.10.4-1 → 1.10.5-r2、`libopenssl3` 3.0.9-13 → 3.0.22-r1（同 soname，ABI 兼容；
  tcpdump / airodump-ng / hcxdumptool 实测均正常）。当前安装器已改成 `--force` 只动 hcxdumptool 本体。
  如需回退 libpcap1：`opkg install --force-downgrade /root/ipk/libpcap1_1.10.4-1_mipsel_24kc.ipk`；
  libopenssl3 3.0.9-13 是第三方固件自带版本、官方 feed 没有，离线无法精确回退。
