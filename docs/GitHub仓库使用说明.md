# GitHub发布与个人学习流程 v4.0

仓库：https://github.com/estenbernhardpj109/xioaxiaoxianshengGZC 。默认分支main，源码在仓库根目录。GitHub托管源码和参赛资料，银行Python服务与MySQL在本机启动；GitHub Pages无法直接运行这套后端。

## 1. 准备环境并克隆

安装Git、Python 3.10及以上、MySQL 8；Node.js只用于可选前端回归，应用不依赖Node服务器。实际回归环境为Windows、Python 3.12和MySQL 8.0。安装后执行：

```powershell
git --version
python --version
git clone https://github.com/estenbernhardpj109/xioaxiaoxianshengGZC.git
cd xioaxiaoxianshengGZC
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果系统策略阻止激活虚拟环境，可以直接使用已安装的Python与requirements启动；无需为本项目关闭系统安全保护。数据库密码、API密钥及经理口令均不写入Git。忽略项包含.venv、.runtime、日志、缓存及实际.env。

## 2. 启动MySQL应用

确认本机MySQL服务正常，运行：

```powershell
.\启动_MYSQL.ps1
```

脚本隐藏输入数据库密码，默认连接127.0.0.1:3306、root用户、root数据库，自动创建15张zx_项目表。首次导入模拟数据，后续保留数据库状态。可用-Database、-DbUser及-Port指定其他本机配置；每个演示库只运行一个业务服务实例。

|页面|地址|体验步骤|
|---|---|---|
|登录注册|http://127.0.0.1:8011/login|自行注册后登录，或明确选择共享演示|
|业务工作台|http://127.0.0.1:8011/app|对话、计划、确认、环境核验|
|数字人|http://127.0.0.1:8011/assistant|询问“如何转账”，再选择办理引导|
|运营大屏|http://127.0.0.1:8011/ops|仅演示身份可查看，经理另验运营口令|

注册用户初始余额0元、无历史记录；演示身份使用完整固定种子样本。不要将MySQL密码当成用户登录密码。页面交易密码123456及同页验证码仅为明确标示的模拟身份因子。经理口令位于本机.runtime/ops_manager_code.txt，不能提交仓库。

开启演练流或DeepSeek：

```powershell
.\启动_MYSQL.ps1 -Simulation
.\启动_MYSQL.ps1 -DeepSeek -Model deepseek-flash
```

两个命令是不同启动方式，按需选择一个，不同时连接同一演示库。DeepSeek模式隐藏输入密钥；无密钥时本地知识问答与规则办理仍可使用。模型名称以账号当前可用服务为准。

## 3. 理解目录与运行链路

backend/auth.py负责身份、scrypt及会话；digital_assistant.py负责有依据的咨询和办理分流；agent目录负责意图、DAG与独立授权；bank是模拟核心。storage.py提供MySQL事务和关系投影，operations.py提供指标与工单。frontend包含统一界面，data是自建模拟快照，tests和evidence分别保存用例与结果，docs和demo保存参赛材料。

先阅读README，再看注册登录与数字人设计说明、作品报告和安全自评。一次学习可以按“注册-登录-咨询-补全意图-生成计划-确认或取消-核对审计”顺序操作。对话与未确认计划在进程内维护，重启后安全清空；登录会话摘要与已授权定时任务存储MySQL。

## 4. 运行检查

业务和HTTP检查使用隔离临时文件：

```powershell
python tests/test_agent.py
python tests/test_http.py
node tests/test_ops_ui.mjs
```

MySQL集成需在当前PowerShell进程隐藏提供密码，然后运行两组测试。它们分别创建随机zx_test_库，结束只清理本次测试库，不使用root演示库：

```powershell
$testSecret=Read-Host '输入本机MySQL密码' -AsSecureString
$testCredential=[System.Net.NetworkCredential]::new('', $testSecret)
$env:MYSQL_PASSWORD=$testCredential.Password
try {
    python tests/test_mysql_ops.py
    python tests/test_auth_digital.py
} finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    Remove-Variable testSecret,testCredential -ErrorAction SilentlyContinue
}
```

当前143项检查由45业务、15HTTP、35MySQL运营、44身份数字人和4前端状态组成。检查JSON保存在evidence。可见界面另有注册、跳转、退出和响应式检查记录，不将截图数量混入自动测试总数。

## 5. 使用自己的GitHub提交身份

安装官方GitHub CLI后登录：

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth status
git config user.name "你的GitHub用户名"
git config user.email "你在GitHub验证的邮箱或账号提供的noreply邮箱"
git remote -v
```

按CLI显示的最新设备码在GitHub官方登录页授权，不在聊天、截图或仓库中保存访问令牌。账号邮箱决定提交关联；仅修改user.name不会改写已经发布的作者。旧历史归属已处理，后续使用普通提交即可，不需要强制推送。

## 6. 修改、检查并推送

```powershell
git status --short
git diff
git add -A
git diff --cached --check
git diff --cached --stat
git commit -m "Describe the final change"
git pull --rebase origin main
git push origin main
```

按本次实际改动选择文件；新增视频、素材或说明也需显式加入。提交前查看暂存差异，确认无口令、密钥、会话、数据库备份及真实个人数据。发生冲突时先修复并继续rebase，不用强制推送覆盖远程。若网络HTTP/2连接不稳定，可对单次Git命令指定`git -c http.version=HTTP/1.1 push origin main`，不修改证书校验。

## 7. 核对远程与参赛包

推送后打开仓库确认最新提交、作者和关键文件；本地工作区应干净。发布提交材料前先更新文档、PPT、视频和evidence，再运行：

```powershell
python scripts/package_submission.py
```

该脚本生成源码完整包和SHA256清单，排除运行状态与凭据，验证ZIP CRC和每个成员哈希。它不会自动上传赛事平台。提交分类为技术文档PDF、答辩PPTX、源码部署ZIP、安全自评PDF，MP4可选。仓库链接不能代替源码压缩包上传；团队身份、参赛资格及平台接收结果由本人核对。

## 8. 后续运行边界

重启前使用Ctrl+C停止服务，MySQL保留账户与业务状态；不要以删除数据目录作为日常恢复方法。备份与离线时钟见部署文档。项目当前为回环地址沙箱，公网服务需另行落实HTTPS、Secure Cookie、生产IAM/MFA、专用数据库权限、银行清算与合规评审。不会通过GitHub推送自动上线真实银行服务。
