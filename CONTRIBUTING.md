# 一起开发路小管

仓库负责人为 **@cc-silence**。日常流程是：建立任务单 → 在独立分支修改 → 提交合并申请（PR）→ 自动检查 → 负责人审核 → 合入 main → 统一打包发布。

本次协作合并评审：[wydev 分支处理理由与复核方法](docs/wydev-integration-review-20261104.md)。

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

本地没有额外提交且工作区干净时，用 git pull --ff-only 或 git merge --ff-only 更新；快进只是移动分支指针，不创建合并提交。存在未提交修改时先检查并保护，不覆盖、不丢弃。

双方都有独立提交时，通过普通合并（Create a merge commit）保留原提交及编号；不默认使用 Squash merge（压缩合并）或 Rebase merge（变基合并）。

每条提交已对应一个明确功能或修复时保留原记录；只有提交过于零碎、需要整理时才考虑压缩，并先与提交者确认。不要重写协作者仍在使用的提交历史。

主分支禁止强制推送、重置覆盖和删除；合并后提醒协作者更新 main，并从最新 main 开新任务分支，避免继续复用已压缩合并的旧分支。

仓库配置必须允许普通合并，不启用 required_linear_history（强制直线历史）；本地配置修改不代表 GitHub 后台已同步，未同步时明确说明，不用压缩合并绕过。

合并与功能修复分开：无冲突时使用 Git 原生快进或普通合并，不由 AI 手工复制、重建双方改动，不借合并顺手重写、重构或删除协作者实现。

存在冲突时，只解决必要的冲突范围，核对共同祖先及双方改动意图；不得整份文件直接选用 ours/theirs（全部用我方或对方）来消除冲突。意图不明确时先确认，不凭猜测舍弃任何一方的功能。

合并审查发现的独立问题，用单独修复提交处理并说明原因、影响及验证结果，便于协作者审查和撤回；不要把无关修复混入合并提交。确需随冲突解决调整的部分，逐项记录理由。

合并完成后，核对原分支提交是否保留、最终差异是否完整、本地未提交修改是否受保护，并运行相关测试及项目要求的检查。Git 合并成功不等于功能验证通过，不得宣称绝对无遗漏或无风险。

## 发布

代码合入后由负责人拉取 main、核对并统一打包发布。CI 的权限仅用于读取代码和执行测试，不持有发布签名或 Cloudflare 凭证，也不会登录真实路由器。

发布继续执行 [发布规则](docs/release-delivery.md)：Windows/Linux 的正式版和 alpha 各保留一版，共最多四份软件包，常用及抓包离线包各保留一版，并检查 1 GB 峰值。完整包及离线包通过现有更新服务交付。

GitHub 分支规则的配置保存在 .github/rulesets/；修改这些文件后，由负责人核对并同步 GitHub 后台设置，CI 不自动修改仓库权限。
