# 一起开发路小管

仓库负责人为 **@cc-silence**。日常流程是：建立任务单 → 在独立分支修改 → 提交合并申请（PR）→ 自动检查 → 负责人审核 → 合入 main → 统一打包发布。

## 领取任务和开发

先在 Issues 建立或认领任务，说明问题、预期行为和验收条件，避免多人同时修改同一部分。修改前阅读 [AGENTS.md](AGENTS.md)，模块自己的规则也需要遵守。

固定协作者接受邀请后，可在仓库内建立分支。其他贡献者可以 Fork，即先复制一份仓库到自己的账号，再提出合并申请。

```powershell
git clone https://github.com/cc-silence/LuXiaoGuan.git
cd LuXiaoGuan
git switch -c fix/wifi-scan
python -m pip install -r requirements-ci.txt
```

用 feature/、fix/、docs/ 或 chore/ 开头命名分支；一个分支解决一个明确问题。开发完成后提交并推送该分支：

```powershell
git add <本次修改的文件>
git commit -m "Fix wireless scan"
git -c http.sslbackend=openssl push -u origin fix/wifi-scan
```

在 GitHub 创建指向 main 的 PR，写清修改原因、验证结果和关联任务单。所有贡献都通过 PR 合入，保留可追溯记录。

## 检查和审核

在 Windows、Python 3.10 环境安装 requirements-ci.txt 后，可运行与改动相关的测试。完整离线回归使用：

```powershell
python scripts/ci_repository_check.py
python scripts/run_compatibility_audit.py --output build/ci --timeout 120
python scripts/ci_repository_check.py --audit-summary build/ci/summary.json
```

GitHub 自动执行源码检查、Python 语法检查和 Windows 离线回归，包含真实 Lua 5.1 解释器下的事务模拟。缺少测试、执行失败、超时或出现非预期跳过都会阻止合并。真实 Windows 屏幕绘制测试需要本机另行验证；离线回归不能代替六个 OpenWrt 系列的真机验证。

涉及路由器的新增或修改功能，必须核对官方 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12 的能力和返回格式。配置写入要验证取消、断线、冲突和失败回滚。视觉变化按 AGENTS.md 先沟通，交付时说明明暗主题与缩放的实际检查结果。

其他人的 PR 需要 @cc-silence 审核；新增提交会使旧批准失效。负责人自己的 PR 在自动检查通过后可由负责人合并，免去自己批准自己的要求。自动检查和主线历史保护始终生效。

采用 Squash merge，也就是把一个 PR 的修改合成一个清晰的主线提交；合并后自动删除已完成的开发分支。讨论中的问题解决后再合并。

## 发布

代码合入后由负责人拉取 main、核对并统一打包发布。CI 的权限仅用于读取代码和执行测试，不持有发布签名或 Cloudflare 凭证，也不会登录真实路由器。

发布继续执行 [发布规则](docs/release-delivery.md)：Windows/Linux 的正式版和 alpha 各保留一版，共最多四份软件包，常用及抓包离线包各保留一版，并检查 1 GB 峰值。完整包及离线包通过现有更新服务交付。

GitHub 分支规则的配置保存在 .github/rulesets/；修改这些文件后，由负责人核对并同步 GitHub 后台设置，CI 不自动修改仓库权限。
