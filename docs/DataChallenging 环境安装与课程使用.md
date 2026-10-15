# 《大数据分析技术》DataChallenging 学生操作指南

**从安装环境到完成 Phase 1 / Exposure / Phase 2**

> **适用对象**：第一次使用 DataChallenging 的课程学生，以及需要回看操作流程的往届学生。  
> **文档版本**：V1.0（2026-10-14）  
> **课程环境基线**：Windows → WSL2 → Ubuntu 22.04 → Docker Engine + Docker Compose → BigData 课程离线包 → `bin/datac`。  
> **发布版本**：示例参考 BigData-v10.1.18；课堂上务必使用教师当期发放的版本。  
> **依据**：《大数据分析技术——DataChallenging 环境安装与课程使用》20 页课堂 PPT；补充了逐步操作、成功检查及常见问题。  
> **阅读建议**：第一次从第 0 节顺序执行；以后只需按当前阶段查对应章节。

> **说明：哪些步骤已明确，哪些还要核对？**  
> PPT 明确给出了 WSL 安装、Docker 的两种安装路线、课程 `scripts/install.sh`、`doctor`、P1 Build/Validate，以及 P1/P2 的 Reference/Agent 运行、Exposure Import/Status/Run 等命令。本文原样保留这些 CLI 入口。Docker 官方源的具体 `apt` 操作是参考官方文档补充的安装说明。PPT 未明确的安装包下载地址、课程网关地址、API Key、镜像文件名、容器服务名、某些自动生成目录和 `review-comments.json` 的字段，本文**不作虚构**；由教师当期 Release、模板和 CLI `--help` 确定。

---

## 目录

1. [先看整门课要完成什么](#1-先看整门课要完成什么)
2. [分清三个终端和两类路径](#2-分清三个终端和两类路径)
3. [安装前准备](#3-安装前准备)
4. [安装 WSL2 和 Ubuntu 22.04](#4-安装-wsl2-和-ubuntu-2204)
5. [在 Ubuntu 中配置 systemd 和基础工具](#5-在-ubuntu-中配置-systemd-和基础工具)
6. [安装 Docker Engine 与 Docker Compose](#6-安装-docker-engine-与-docker-compose)
7. [安装并启动 BigData 课程离线环境](#7-安装并启动-bigdata-课程离线环境)
8. [配置课程 LLM API](#8-配置课程-llm-api)
9. [运行 doctor，确认环境可用](#9-运行-doctor确认环境可用)
10. [认识并检查课程工作区](#10-认识并检查课程工作区)
11. [Phase 1：填写身份，设计 Task 和 Agent](#11-phase-1填写身份设计-task-和-agent)
12. [Phase 1：先测 Reference，再运行 Agent](#12-phase-1先测-reference再运行-agent)
13. [Phase 1：Build、Validate 和提交](#13-phase-1buildvalidate-和提交)
14. [Exposure：导入并运行 10 个匿名 Task](#14-exposure导入并运行-10-个匿名-task)
15. [分析失败并完成 Review](#15-分析失败并完成-review)
16. [Phase 2：完成 Task v2、Agent v2 和实验报告](#16-phase-2完成-task-v2agent-v2-和实验报告)
17. [Phase 2：测试、Build、Validate 和提交](#17-phase-2测试buildvalidate-和提交)
18. [常见问题和分层排错](#18-常见问题和分层排错)
19. [阶段完成检查表](#19-阶段完成检查表)
20. [全部常用命令速查](#20-全部常用命令速查)
21. [附录：Failure Mode 与官方参考文档](#21-附录failure-mode-与官方参考文档)

---

## 1. 先看整门课要完成什么

这门课不只是安装软件。你要用同一套 DataChallenging 环境，完成一次“设计任务—编写 Agent—运行—发现问题—改进—提交”的实验。

```text
准备课程离线包与 API 信息
          ↓
安装 WSL2 / Ubuntu 22.04
          ↓
安装 Docker Engine + Compose
          ↓
安装 BigData 课程包并启动
          ↓
bin/datac doctor   →  环境检查通过
          ↓
Phase 1
  写 Task v1（题目 + 数据 + 标准答案）
  写 Agent v1
  Reference Run → Agent Run
  Build → Validate → 提交 P1
          ↓
Exposure
  收到教师/助教发放的匿名任务包
  Import → Status → Run
  用同一个 Agent v1 跑 10 个陌生 Task
  分析错误，完成审稿记录
          ↓
Phase 2
  完成 Task v2 / Agent v2
  填写 review-comments.json 和实验报告
  Reference Run → Agent Run
  Build → Validate → 提交 P2
```

**你最终要交的是两个阶段的正式提交包，不是把整个 Workspace 文件夹随意压缩上交。** 第一个阶段提交 P1，第二个阶段提交 P2。Exposure 是两阶段之间的匿名任务实践，其证据和 Review 服务于 P2。

### 1.1 四个关键词

| 名称 | 在这门课里是什么意思 | 你要做什么 |
|---|---|---|
| Task | 一道可执行的数据分析任务 | 设计题目、准备 `data.csv`、编写 `reference.py` |
| Agent | 尽可能通用的解题程序 | 编写/修改 `agent.py` 并运行测试 |
| Reference / Ground Truth | 题目的标准答案计算程序与正确结果 | **先确保 Reference 正确**，再看 Agent 表现 |
| Exposure | 10 道其他人设计的匿名任务 | 用自己的 **Agent v1** 运行，分析成功与失败原因 |

---

## 2. 分清三个终端和两类路径

**很多安装失败不是代码出错，而是命令在错误的地方执行。** 本手册采用以下标记。

| 标记 | 在哪里输入命令 | 示例 |
|---|---|---|
| **[Windows PowerShell]** | Windows 开始菜单打开 PowerShell；安装 WSL 时使用管理员权限 | `wsl -l -v` |
| **[Ubuntu]** | 进入 WSL 的 Ubuntu 22.04 终端 | `sudo apt update`、`systemctl`、`docker compose` |
| **[课程 CLI]** | 能访问当前 Release 中 `bin/datac` 的课程命令终端（按教师提供的进入方式） | `bin/datac doctor --workspace /workspace` |

### 2.1 不要直接照抄示例路径

手册中会出现以下两种路径：

- `~/...`、`/mnt/d/...`：示例 Ubuntu/WSL 文件路径。你需要替换为电脑中**真正存放 Release 的位置**。
- `/workspace/...`：DataChallenging 课程 CLI 使用的工作区绝对路径；**不一定就是 Windows 文件管理器中的 `C:\workspace`**。需要在当前执行环境内确认映射关系。

例如，课程 Release 存在 Windows 的 `D:\Course\BigData-v10.1.18` 时，在 WSL 中常对应：

```bash
cd /mnt/d/Course/BigData-v10.1.18
```

但这是**说明路径转换原理的例子**，不是每个人都存在的真实目录。

### 2.2 进入一个目录之后，先确认自己在哪里

**[Ubuntu 或课程 CLI]**

```bash
pwd
ls -la
```

运行课程命令前，至少能确认当前 Release 中存在 `bin/datac`：

```bash
ls -l bin/datac
```

如果找不到，先检查工作目录和课程启动方式，不要从网上随意下载另一个同名程序替换。

---

## 3. 安装前准备

正式开始前，先按下面四项检查。

### 3.1 Windows 版本满足要求

使用 Windows 11，或 Windows 10 版本 2004 / Build 19041 及以上。较旧系统不要直接开始安装，应先升级系统或按照教师提供的替代方案处理。

**[Windows PowerShell]**

```powershell
winver
```

会打开系统版本窗口。记录自己的 Windows 版本与系统内部版本号。

### 3.2 检查 CPU 虚拟化

在 Windows 中按 `Ctrl + Shift + Esc` 打开任务管理器，依次进入**性能 → CPU**，确认“虚拟化：已启用”。

若显示“已禁用”，需要按设备厂商或学校机房要求开启 BIOS/UEFI 虚拟化。不要为了排错随意修改不熟悉的 BIOS 选项。

### 3.3 提前获得完整 Release

向教师获取 **BigData-v10.1.XX 的完整课程离线包**。至少检查 Release 目录中是否能找到与当前版本说明一致的：

```text
BigData-v10.1.XX/
├── images/              # 课程镜像归档
├── docker-compose.yml  # Compose 配置
├── scripts/             # 安装脚本
├── bin/                 # 课程命令入口（依实际 Release）
└── ...                  # 其余真实发布文件
```

**不要自己猜镜像文件名、镜像版本或替换版本。** 文中的 `BigData-v10.1.XX` 表示当期教师发放的 Release，具体名称按实际情况填写。

### 3.4 提前获取课程 API 信息

至少准备：

```text
LLM_BASE_URL
LLM_API_KEY
```

由教师给出当前课程允许使用的网关、密钥取得方式和模型策略。不要直接使用网上搜到的任意接口地址，也不要把别人的 API Key 当作自己的长期配置。

> **本节完成标准**：Windows 版本合适、虚拟化开启、完整 Release 已到手、课程 API 领取方式已明确。

---

## 4. 安装 WSL2 和 Ubuntu 22.04

> 本节的 `wsl` 命令全部在 **Windows PowerShell（管理员）** 中执行，不要在 Ubuntu Bash 里原样执行。

### 第 1 步：以管理员身份打开 PowerShell

在开始菜单搜索 `PowerShell`，右键选择“以管理员身份运行”。

### 第 2 步：更新 WSL 并查看发行版

**[Windows PowerShell（管理员）]**

```powershell
wsl --update
wsl --list --online
```

如果列表中能看到 `Ubuntu-22.04`，继续安装。

### 第 3 步：安装指定的 Ubuntu 版本

```powershell
wsl --install -d Ubuntu-22.04
```

若系统要求重启，先保存工作并重启 Windows。首次打开 Ubuntu 时，按提示完成 Linux 用户名和密码的创建。

**注意**：输入 Ubuntu 密码时，终端通常不会显示星号或字符，这是正常现象。

### 第 4 步：确认安装成功且使用 WSL2

```powershell
wsl -l -v
```

预期重点：能够找到 `Ubuntu-22.04`，且 **VERSION 为 2**。

若已经安装却显示 VERSION = 1，可在核对数据及学校设备要求后使用：

```powershell
wsl --set-version Ubuntu-22.04 2
```

### 第 5 步：进入 Ubuntu

```powershell
wsl -d Ubuntu-22.04
```

看到 Linux Shell 提示符后，再继续下面的 Ubuntu 命令。

### 安装失败怎么办？

如果下载长时间停在 0%、Microsoft Store 无法访问，可参考微软提供的 Web 下载选项：

```powershell
wsl --install --web-download -d Ubuntu-22.04
```

不要反复执行会破坏现有 Ubuntu 数据的 `wsl --unregister`。如果已在 Ubuntu 中存放重要课程文件，先备份，再处理安装或发行版损坏问题。

> **本节完成标准**：`wsl -l -v` 能看到 `Ubuntu-22.04` 且 VERSION = 2。

---

## 5. 在 Ubuntu 中配置 systemd 和基础工具

> 从本节开始，命令运行环境改为 **Ubuntu**。

### 第 1 步：检查当前用户和发行版

**[Ubuntu]**

```bash
whoami
cat /etc/os-release
```

确认系统为 Ubuntu 22.04，并记下当前用户名。后续示例使用 `sudo`，如你的用户没有 sudo 权限，应先按学校提供的账号配置解决。

### 第 2 步：检查 systemd

```bash
ps -p 1 -o comm=
```

期望输出：

```text
systemd
```

若已经是 `systemd`，**不要再修改配置**，直接进入下一步。

### 第 3 步：仅在 systemd 未启用时调整配置

用编辑器打开配置文件（保留文件中已有的其他有效配置）：

```bash
sudo nano /etc/wsl.conf
```

确保存在：

```ini
[boot]
systemd=true
```

保存后退出 Ubuntu，回到 **Windows PowerShell**：

```powershell
wsl --shutdown
```

重新进入 Ubuntu：

```powershell
wsl -d Ubuntu-22.04
```

再次检查：

```bash
ps -p 1 -o comm=
```

如果仍不是 `systemd`，先检查 WSL 版本和 `/etc/wsl.conf` 是否正确，不要继续安装 Docker。

### 第 4 步：安装基础工具

**[Ubuntu]**

```bash
sudo apt update
sudo apt install -y ca-certificates curl wget git unzip gnupg
```

如果老师发放的 Release 要求其他工具，按发布说明补齐。

> **本节完成标准**：Ubuntu 版本正确、`ps -p 1 -o comm=` 返回 `systemd`、基础命令可用。

---

## 6. 安装 Docker Engine 与 Docker Compose

课程要求在 **WSL Ubuntu 内使用 Docker Engine 和 Docker Compose**，不以 Docker Desktop 为安装前提。只选择下面一条安装路线；**不要把 Docker 官方仓库包与 Ubuntu 仓库包混装**。

### 6.1 安装路线 A：Docker 官方 apt 源（建议按教师环境要求选择）

这一节的仓库和软件包命令来自 Docker 官方 Ubuntu 安装文档；安装前需能正常访问相应软件源。

**[Ubuntu] 第 1 步：配置 Docker 官方密钥及软件源**

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

接着执行：

```bash
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF_DOCKER
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF_DOCKER

sudo apt update
```

**重要**：以上步骤适用于尚未安装其他互相冲突的 Docker 包的新环境。已经安装 Docker 的机器，不要直接替换软件源；应先确认已有安装来源并按官方迁移说明处理。

**[Ubuntu] 第 2 步：安装 Engine、CLI、Buildx 和 Compose 插件**

```bash
sudo apt install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

### 6.2 安装路线 B：Ubuntu 仓库

如果教师统一使用 Ubuntu 仓库路线，可尝试：

```bash
sudo apt update
apt-cache policy docker.io docker-compose-v2
```

先检查软件源中是否存在 `docker-compose-v2` 的可安装候选版本。如果存在，再执行：

```bash
sudo apt install -y docker.io docker-compose-v2
```

若 `docker-compose-v2` 没有候选包，停止这一安装路线，改按教师确定的版本或官方源方案处理。**不要把旧式 `docker-compose` 命令误认为 `docker compose` 插件已经可用。**

### 6.3 安装后必须完成四项检查

**[Ubuntu]**

```bash
sudo systemctl enable --now docker
sudo systemctl is-active docker
sudo docker version
sudo docker compose version
sudo docker ps
```

重点检查：

1. `systemctl is-active docker` 返回 `active`。
2. `docker version` 能显示 **Client 和 Server** 的版本信息。
3. `docker compose version` 能显示 Compose 版本。
4. `docker ps` 能连接 Docker daemon，即使当前没有运行的容器也不应报连接错误。

> 如果教师的课程安装脚本需要你以普通用户直接使用 `docker`，应按照课程统一的权限方案配置。将用户加入 `docker` 组意味着授予近似 root 的权限，不能在公共机器上随意执行；不确定时优先联系助教，而不是反复执行 `sudo chmod 777 /var/run/docker.sock`。

> **本节完成标准**：Docker 服务 `active`，Engine 的 Server 可见，`docker compose version` 正常，`docker ps` 不报 daemon 连接错误。

---

## 7. 安装并启动 BigData 课程离线环境

### 第 1 步：找到你收到的课程 Release

**[Ubuntu]**

进入真实的课程包目录。例如：

```bash
cd /mnt/d/Course/BigData-v10.1.18
```

该路径仅作示例，请替换成你的实际目录。然后检查：

```bash
pwd
ls -lah
ls -lah images
ls -lah scripts
ls -l docker-compose.yml
```

要确认的是：`images/`、`scripts/` 和 `docker-compose.yml` 属于**同一个 Release 版本**。

### 第 2 步：优先运行课程一键安装脚本

**[Ubuntu，位于 Release 根目录]**

```bash
bash scripts/install.sh
```

如果 Release 中的脚本具有执行权限，也可按课堂 PPT 中的方式运行：

```bash
scripts/install.sh
```

根据 20 页 PPT，课程脚本预期依次完成：

1. 加载离线课程 Docker 镜像；
2. 启动课程容器；
3. 运行环境检查（doctor）；
4. 输出通过状态或错误提示。

**不要在执行前自行猜测镜像标签和镜像名。** 如课程脚本成功完成，就不需要重复手动 `docker load`。

### 第 3 步：确认课程容器正在运行

**[Ubuntu，位于 Release 根目录]**

```bash
docker compose ps
```

如普通用户缺乏 Docker 权限，应按照课程授权方式执行；可以用 `sudo docker compose ps` 做诊断，但不要长期在两种权限模式下混用不明来源的脚本。

**成功判断**：Compose 所管理的课程服务按实际发布要求启动，关键服务不处于反复重启或退出状态。具体服务名和数量以当期 `docker-compose.yml` 为准。

### 7.1 仅在一键安装失败、助教要求手动排查时使用

PPT 给出的手动等价方向是：

```bash
docker load -i images/<课程镜像>.tar
docker compose up -d
docker compose ps
```

其中 `<课程镜像>.tar` 只是占位符，必须先查看 `images/` 目录找到**实际文件名**；文件可能采用其他归档命名，不能不经核实照抄。本节不能取代教师 Release 的安装说明。

### 7.2 离线与联网的边界

- “课程包离线安装”表示镜像和课程依赖可以随 Release 提供，不要求现场从镜像仓库下载。
- **不表示模型推理离线**。Agent 后续调用课程 LLM 网关时仍需网络和正确的 API 配置。

> **本节完成标准**：课程镜像可加载，Compose 服务能启动，并可以进入下一节运行 `bin/datac`。

---

## 8. 配置课程 LLM API

### 第 1 步：找到 Workspace 的 `.env`

根据课程 PPT，API 配置文件位于课程工作区根部：

```text
workspace/.env
```

如果你的 Release 根目录能看到 `workspace/`，可在对应的 **Ubuntu** 目录检查：

```bash
ls -la workspace
ls -la workspace/.env*
```

如果课程 CLI 运行环境将工作区挂载为 `/workspace`，则可在该环境中检查：

```bash
ls -la /workspace/.env*
```

二者应以实际部署的挂载关系为准；不要凭感觉在 Windows 和容器中分别创建两套互不相关的 `.env`。

### 第 2 步：编辑 `.env`

在**当前实际生效的 Workspace** 下打开 `.env`。例如位于 Release 的 Workspace：

```bash
nano workspace/.env
```

或在实际挂载为 `/workspace` 的课程命令环境中：

```bash
nano /workspace/.env
```

按教师提供的信息填写：

```dotenv
LLM_BASE_URL=这里填写教师提供的课程网关地址
LLM_API_KEY=这里填写你获得的课程密钥
```

`这里填写...` 不能保留为真实配置。其他字段（如模型、超时、Token 限额）按照 `.env.example` 和课程 Policy 填写或保持默认，**不要自行绕过统一模型策略**。

### 第 3 步：保护自己的密钥

- 不要把真实 `.env` 提交到 Git、上传公开仓库或打进 P1/P2 提交包；
- 不要在截图、群聊、实验报告中粘贴真实 Key；
- 排错时可以发送错误码和脱敏日志，不要公开请求头中的密钥；
- `.env.example` 可以展示字段名，但不能填真实密钥。

### 第 4 步：理解 doctor 和实际 Agent 调用的差别

`doctor` 用于环境健康检查；它是否测试到具体 LLM 网关，需要看实际版本的输出。即使 `doctor` 通过，也不等于真实 Agent 一定可以成功调用课程 API。后续仍需通过 `bin/datac run --phase phase1` 做端到端验证。

> **本节完成标准**：实际生效的 `.env` 已填入自己的课程配置，密钥没有泄漏，并能进行下一节 CLI 检查。

---

## 9. 运行 doctor，确认环境可用

**[课程 CLI，位于 `bin/datac` 所在的 Release/课程命令根目录]**

### 第 1 步：看 CLI 是否存在

```bash
bin/datac --help
```

**成功判断**：能输出当前版本公开的课程命令帮助，不报 `command not found` 或文件不存在。

### 第 2 步：运行课程环境检查

```bash
bin/datac doctor --workspace /workspace
```

看实际输出中有关 Workspace、Python/Runtime、Policy、Docker/环境等检查的结果。课堂 PPT 的目标是 **doctor PASS**。

**请区分两种结果**：

- **命令执行成功**：没有 CLI 路径或程序启动错误；
- **环境检查通过**：doctor 中要求的关键检查全部通过。

不要因为命令能执行，就认定环境已经可以做完课程实验。

### 第 3 步：首次安装留档

建议在自己的笔记中记录：

```text
课程 Release 版本：____________
Windows 版本：_________________
WSL Ubuntu 版本：______________
Docker Engine：________________
Docker Compose：_______________
doctor 日期：__________________
doctor 结果：PASS / FAIL
失败项和处理记录：____________
```

> **本节完成标准**：`bin/datac --help` 能显示帮助，`bin/datac doctor --workspace /workspace` 的关键检查通过。

---

## 10. 认识并检查课程工作区

### 10.1 不需要反复初始化 Workspace

课堂 PPT 特别强调：**课程镜像已经准备好 Phase 1、Phase 2 工作区，不要重复运行旧的 `init workspace` 流程**，防止覆盖已有文件。

工作区逻辑结构如下（实际版本可以有额外系统管理文件）：

```text
workspace/
├── .env                   # 课程 API 配置；不打包
├── .env.example           # 配置模板
├── README.md              # 使用说明（如版本中提供）
├── phase1/
│   ├── task.json          # Task v1 题目与答案
│   ├── data.csv           # Task v1 数据
│   ├── reference.py       # Task v1 标准答案程序
│   ├── agent.py           # Agent v1
│   └── metadata.json      # Phase 1 身份和设计元数据
├── phase2/
│   ├── task.json          # Task v2
│   ├── data.csv
│   ├── reference.py
│   ├── agent.py            # Agent v2
│   ├── metadata.json
│   ├── review-comments.json
│   └── 实验报告模板.docx
├── exposure/              # 匿名 Task 与分配信息
├── runs/                  # 程序产生的运行记录
└── store/                 # 包与 Build 缓存等系统内容
```

### 10.2 每个目录的用途

| 路径 | 用途 | 主要由谁维护 | 是否放入最终 P1/P2 |
|---|---|---|---|
| `phase1/` | 第一阶段 Task v1 + Agent v1 | 你 | P1 Build 取本阶段规定文件 |
| `phase2/` | 最终 Task v2 + Agent v2、Review、报告 | 你 | P2 Build 取本阶段规定文件 |
| `exposure/` | 匿名任务导入、分配 | 系统；你按命令操作 | 不直接整目录打包 |
| `runs/` | 执行过程、输出与诊断证据 | 系统 | 不直接整目录打包 |
| `store/` | 原包、缓存、Build 产物等 | 系统 | 不直接整目录打包 |
| `.env` | LLM Key/网关 | 你 | **绝不打包** |

### 10.3 先看一眼文件是否齐全

**[课程 CLI]**

```bash
ls -la /workspace/phase1
ls -la /workspace/phase2
ls -la /workspace/exposure
```

如果命令环境没有 `/workspace`，请先核对当前镜像/挂载。不要自行在不相关目录建一个空的 `/workspace` 让命令“看起来有目录”。

> **本节完成标准**：知道自己的 P1、P2、Exposure 分别在哪里，并且能够区分学生维护文件与系统生成文件。

---

## 11. Phase 1：填写身份，设计 Task 和 Agent

### 第 1 步：先填写 `phase1/metadata.json`

打开你工作区中的：

```text
/workspace/phase1/metadata.json
```

把模板学号替换为自己的真实学号，确保 `student_id`、`task_id`、`agent_id` 彼此对应。结合后续冻结的设计元数据约定，示例如下：

```json
{
  "workspace": "phase1",
  "student_id": "20250001",
  "task_id": "TASK_20250001",
  "agent_id": "AGENT_20250001",
  "hypothesized_failure_mode": "FM_AGGREGATION"
}
```

**这是示例，不是你的真实学号。** 请注意两点：

1. 课堂 PPT 的简化示例重点展示了 `student_id`、`task_id`、`agent_id`；`workspace` 和 `hypothesized_failure_mode` 的完整字段约定来自后续课程协议，应以**当前版本随包模板和 Validator** 为准。
2. 在该后续协议中，`hypothesized_failure_mode` 属于外层 Metadata，**不应再写进 `task.json`**。合法 Failure Mode 使用 `FM_AGGREGATION` 这类无数字 Key，不用旧式 `FM_06_AGGREGATION`。

如果现有模板与示例不一致，先查当期课程的 schema 和 `--help`，不要通过随意添加、删除字段来“凑”校验通过。

### 第 2 步：编写 `task.json`

`task.json` 负责描述**题目是什么、应该怎么做、正确结果是什么**。建议把要求写到能让没有见过你数据的同学独立理解。

示例：

```json
{
  "description": "统计已完成订单的总金额。",
  "task_statement": "读取 data.csv，只保留 status 为 completed 的订单，计算 amount 列的总金额，并按要求输出结果。",
  "answer": 1479.98
}
```

以上数值仅用于解释字段，**不能直接作为你自己 Task 的标准答案**。正确答案必须由自己的数据和 `reference.py` 真实计算得出。

编写题目时自查：

- 有没有说明 CSV 的关键列和数据含义？
- 有没有写明筛选条件、排序/分组/去重规则？
- 有没有明确数值精度、缺失值和输出格式？
- 是否存在多个合理答案或题意歧义？

### 第 3 步：准备 `data.csv`

CSV 是 Task 的数据依据，不要只写题目、却缺少题目中引用的列。检查文件头、编码及必要样例：

```bash
head -n 5 /workspace/phase1/data.csv
```

`head` 只是辅助查看前几行，不能证明数据计算一定正确。实际数据检查仍应由 Reference 和课程 Validator 完成。

### 第 4 步：编写 `reference.py`

`reference.py` 用于**确定性地产生 Ground Truth**。按照当前课程程序接口完成标准答案计算，保持结果可重复，不要调用 LLM 代替标准答案。

注意：本手册不擅自给出新的 `reference.py` 函数签名或库导入方式；具体接口以 Release 自带 Demo 和 Validator 为准。参考程序必须和 `data.csv`、`task.json.answer` 一致。

### 第 5 步：编写 `agent.py`

`agent.py` 是你的解题 Agent。它需要尽可能使用**通用的数据理解与分析方法**，而不是把你自己的 `task.json.answer` 写死返回。

运行 Agent 时，应通过系统提供的接口读取允许公开的 Task 描述和数据，不应尝试读取 Reference、标准答案或作者隐藏设计元数据。保留 Release 所要求的 Agent `solve` 接口，不能凭空替换成自己设计的另一套调用接口。

> **本节完成标准**：P1 五个核心文件完整，身份已改成自己的学号，题目能由 CSV 支撑，Reference 不依赖 LLM，Agent 遵守课程接口。

---

## 12. Phase 1：先测 Reference，再运行 Agent

**顺序固定：先证明你的 Task 有正确的标准答案，再评价 Agent 表现。**

### 第 1 步：运行 Phase 1 Reference

**[课程 CLI]**

```bash
bin/datac reference run --phase phase1
```

这个命令使用 Phase 1 中的 `data.csv` 与 `reference.py`，应在控制台显示参考程序执行结果；若当前版本支持答案核对，还会与 `task.json.answer` 比较。

**你需要检查**：

- Reference 是否正常执行；
- 控制台返回的实际结果是多少；
- `task.json.answer` 是否与实际结果相符；
- 是否有缺失列、数据类型、路径或 Python 异常。

如果 Reference 算出的结果与填写的 `answer` 不同，先检查题意、数据和计算代码，再修改答案。**不能因为 Agent 输出了另一个数，就把 Reference 改成那个数来迎合 Agent。**

### 第 2 步：运行 Phase 1 Agent

```bash
bin/datac run --phase phase1
```

检查输出或运行记录，区分三种情况：

| 情况 | 含义 | 先检查哪里 |
|---|---|---|
| 正确完成 | Agent 正常结束，答案与 Ground Truth 一致 | 记录成功情况 |
| 答案错误 | 程序跑完，但答案不正确 | 题意理解、列名、筛选、聚合、数值精度、格式 |
| 运行失败 | Agent 没有正常结束 | Python 异常、接口、API、文件路径、超时、容器 |

**`run` 命令没有报错，不等于最终答案正确。** 要对照实际输出和标准答案评价。

### 第 3 步：查看本次运行证据

如果当前版本按约定将运行结果保存于 `runs/phase1/`，可先检查：

```bash
ls -lat /workspace/runs/phase1
```

实际输出文件名、内容与目录层级按课程版本为准。不要随意删除运行记录；它们能帮助你分析 Agent 的失败原因。

### 第 4 步：根据结果修改

可以循环执行：

```text
修改 task.json / data.csv / reference.py / agent.py
          ↓
reference run --phase phase1
          ↓
run --phase phase1
          ↓
检查答案与运行日志
          ↓
必要时继续修改
```

不要把 `runs/` 中自动生成的 `result.json` 当成你应该直接修改的答案文件。

> **本节完成标准**：Reference 已正确计算 Ground Truth，Agent 已实际运行，成功或失败都能说清原因。

---

## 13. Phase 1：Build、Validate 和提交

### 第 1 步：确认自己准备提交的是 P1

检查：

```bash
ls -la /workspace/phase1
```

P1 阶段文件应能定位到：

```text
task.json
data.csv
reference.py
agent.py
metadata.json
```

`.env`、`runs/`、`exposure/` 等不是 P1 正式提交内容。

### 第 2 步：运行系统 Build

**[课程 CLI]**（把示例学号替换为自己的学号）

```bash
bin/datac submission build \
  --input /workspace/phase1 \
  --student-id 20250001 \
  --phase P1
```

注意：这里 `--phase P1` 采用 PPT 给出的提交命令写法；它与 Reference/Agent 命令使用的 `--phase phase1` **不是同一种字符串表示**，不要自行替换。

### 第 3 步：找到系统真正生成的 P1 总包

认真阅读 Build 控制台输出，记录**系统打印的完整产物路径**，不要猜测产物一定在某个固定目录。

按照课程冻结的提交约定，P1 的逻辑总包结构是：

```text
bigdata_p1_20250001.tar.gz
├── Tasks_20250001.tar.gz
├── Agent_20250001.tar.gz
└── metadata.json
```

实际压缩扩展名、是否带封装目录前缀，以当前 Release 的真实产物为准。**不要手动修改或重新压缩 Build 产物**。

### 第 4 步：对“真正提交的包”运行 Validate

```bash
bin/datac submission validate \
  --input /把这里换成Build实际输出的P1文件路径 \
  --phase P1
```

**禁止直接照抄上述占位路径。** 例如若 Build 真正输出 `/workspace/store/builds/bigdata_p1_20250001.tar.gz`，才把 `--input` 替换成这个完整路径。该目录仅用于演示如何替换参数，不代表所有版本的默认路径。

Validate 应检查的是**最终总包**，不是只检查你编辑过的 `phase1/` 文件夹。

### 第 5 步：提交同一个文件

核对最终文件名、学号、阶段和 Validate 结果，然后把**刚刚验证通过的同一个 Build 文件**按教师指定平台或方式提交。

不要在 Validate 之后再手动增删文件、改名覆盖内容或者重新压缩；一旦修改内容，就必须重新 Build 和 Validate。

> **本节完成标准**：P1 总包由系统构建、已通过对总包的验证、最终提交的是同一份文件。

---

## 14. Exposure：导入并运行 10 个匿名 Task

Phase 1 之后，你会从助教处收到属于自己的 Exposure 匿名任务包。这里的目标不是更改别人的 Task，而是用**自己已经完成的 Agent v1**面对 10 道陌生任务，观察真实表现。

### 第 1 步：保存自己的 P1 成果

在导入 Exposure 前，先确认 P1 已提交并保留备份。不要因后续实验将 `phase1/agent.py` 或 `phase1/task.json` 覆盖掉。

### 第 2 步：获取并辨认教师发放的 Exposure 包

保存教师/助教发放给你的**真实 Exposure 包**，记录实际路径。不要把其他学生的任务原包直接解压到 `phase1/`。

你在分配任务中看到的 `AT002`、`AT028` 等是匿名 Task ID，不是作者学号，也不是原始 `TASK_学号`。

### 第 3 步：导入匿名任务包

**[课程 CLI]**

```bash
bin/datac exposure import \
  --package /这里替换成教师发放的实际Exposure包路径 \
  --workspace /workspace
```

`--package` 后面必须是实际收到的文件路径。不要把 `/这里替换...` 当成可以直接运行的路径。

导入失败时，先检查包是否完整、路径是否正确、是否已经导入过相同任务，不要直接复制或覆盖 `phase1` 文件。

### 第 4 步：检查匿名任务的导入状态

```bash
bin/datac exposure status --workspace /workspace
```

根据 PPT，目标是确认分配的 **10/10 道 Task 已经导入或登记**。具体输出字段以当前版本实际打印内容为准。

如果不足 10 道：

1. 核对助教发放给你的包是否正确；
2. 查看 Import 是否提示失败；
3. 避免自行补造 `ATxxx` 任务；
4. 联系助教处理分配或包问题。

### 第 5 步：用同一个 Agent v1 执行 Exposure

```bash
bin/datac exposure run --workspace /workspace
```

PPT 所规定的流程是通过 Exposure 命令统一运行匿名任务。**不要把匿名任务复制进 `phase1/`，更不能以覆盖、运行、再恢复文件的方式测试**。

Exposure 的教学要求是：

```text
自己的 Agent v1
     +
10 道匿名 Task
     ↓
实际运行
     ↓
按 ATxxx 保存结果与错误证据
```

### 第 6 步：再次检查状态

```bash
bin/datac exposure status --workspace /workspace
```

PPT 给出的目标是 `tasks = 10 / 10`，执行后 `done = 10`。**完成 10 次运行不等于答对 10 道题**；还需要分析每一道的正确性或失败原因。

> **本节完成标准**：已导入 10 道匿名 Task、同一个 Agent v1 已完成对应运行、每一道有可追溯的执行结果。

---

## 15. 分析失败并完成 Review

Exposure 不以“把 10 个任务跑完”为终点。你还需要读懂结果，判断问题属于 Task 设计、Agent 求解还是运行环境。

### 15.1 对每一道匿名 Task 做四步记录

1. **读题**：明确匿名 Task 要求、数据列、计算规则和输出格式。
2. **看运行**：记录 Agent 是否正常结束，运行输出是什么。
3. **定位失败**：若不正确，识别问题究竟出在需求理解、字段含义、过滤、聚合、数值精度等何处。
4. **写评价**：根据运行证据完成对应 AT ID 的 Review，而不是根据感觉打分。

建议在自己的草稿记录中使用：

| 匿名 Task | 是否正常执行 | 答案是否正确 | 观察到的问题 | 有什么证据 | 对改进 Agent 的启示 |
|---|---|---|---|---|---|
| AT002 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| AT028 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |

**上表只是供你分析的笔记格式，不是正式 `review-comments.json` 的 JSON Schema。**

### 15.2 正式填写 `review-comments.json`

打开课程已经提供的：

```text
/workspace/phase2/review-comments.json
```

按教师/Release 提供的**正式模板**填写 Review。PPT 要求按匿名 `ATxxx` 记录评分与评语，并覆盖你实际被分配的 10 道任务。

目前 PPT 没有给出 `review-comments.json` 的完整字段结构，所以不要从本手册的上表自行拼接成 JSON 冒充正式协议。必须以当前版本模板、示例和 Validator 为准。

最低限度要核对：

- 10 道任务都有记录；
- 匿名 Task ID 与分配给自己的列表一致；
- 没有漏评、重复评、误评其他人的任务；
- 评价尽量引用真实运行证据；
- 不能以猜测代替程序输出或错误日志。

### 15.3 理解什么叫“改进有依据”

如果你发现 Agent v1 在陌生任务上经常把日期列当字符串比较，Phase 2 的改进就应围绕日期解析与校验；如果 Agent v1 在全部任务上表现稳定，也不要求为了形式强行改动。

> **本节完成标准**：你能说明每道匿名 Task 的运行情况，完成 10 道任务的正式 Review，并提炼出有证据支持的 v2 改进方向。

---

## 16. Phase 2：完成 Task v2、Agent v2 和实验报告

### 第 1 步：只在 `phase2/` 中维护最终文件

Phase 2 使用：

```text
/workspace/phase2/
```

请检查应有的文件：

```text
task.json
data.csv
reference.py
agent.py
metadata.json
review-comments.json
实验报告模板.docx
```

不要把最终文件命名成 `agent_v2.py`、`task_v2.json` 来取代系统规定的 `agent.py`、`task.json`。版本由阶段目录和 Metadata 共同表达。

### 第 2 步：完成 Task v2

可根据 Task 质量问题修改 `task.json`、`data.csv` 和 `reference.py`。例如，若 Phase 1 题目有歧义，应补齐必要的字段解释和输出约束。

如果原任务已经正确且清晰，可以保留，但需要在实验报告中解释你对其质量的判断。

### 第 3 步：完成 Agent v2

基于 Exposure 的运行证据修改 `agent.py`，例如加强数据类型、字段映射、筛选条件、结果校验或错误处理。

**不要为了在十道已知匿名题上得分而写死答案**。Agent v2 仍应面向陌生数据分析任务。

### 第 4 步：核对 `phase2/metadata.json`

P2 与 P1 沿用同一个学生身份、`task_id` 和 `agent_id`。后续提交协议还要求 P2 管理 Review ID。示例：

```json
{
  "workspace": "phase2",
  "student_id": "20250001",
  "task_id": "TASK_20250001",
  "agent_id": "AGENT_20250001",
  "review-comments_id": "REVIEW_20250001",
  "hypothesized_failure_mode": "FM_AGGREGATION"
}
```

`hypothesized_failure_mode` 是否可以在 P2 更新、是否需要保留 P1 原始假设，按当期课程实验规则和 Metadata 模板处理，不能随意改写历史记录。

### 第 5 步：填写实验报告

使用课程提供的 `实验报告模板.docx`。至少围绕如下问题组织内容（具体栏目以正式模板为准）：

- Phase 1 你设计了什么 Task，Agent v1 如何运行？
- Exposure 十个陌生 Task 暴露了哪些成功和失败现象？
- 你判断失败原因的依据是什么？
- Task v2 / Agent v2 修改了什么，为什么修改？
- 如果没有修改，为什么认为原方案无需修改？
- Phase 2 实测结果是否支持你的结论？

请保留真实运行证据，不要声称未实际执行的测试已经通过。

> **本节完成标准**：Phase 2 七个核心文件完整，Task/Agent 的变化有证据，Review 覆盖全部分配任务，实验报告已填写。

---

## 17. Phase 2：测试、Build、Validate 和提交

### 第 1 步：运行 Phase 2 Reference

**[课程 CLI]**

```bash
bin/datac reference run --phase phase2
```

确认最终 `data.csv`、`reference.py` 与 `task.json.answer` 能形成一致、确定的 Ground Truth。

### 第 2 步：运行 Phase 2 Agent

```bash
bin/datac run --phase phase2
```

同样要区分：正常且正确、正常但答案错误、执行失败。即使你的 Agent v2 与 v1 完全相同，也应对 Phase 2 正式工作区再运行一次。

### 第 3 步：检查 P2 正式输入

```bash
ls -la /workspace/phase2
```

重点检查 `review-comments.json` 和实验报告是否真的完成，而不是空模板。

### 第 4 步：按同一提交体系 Build P2

课堂 PPT 明确要求 P2 在 `phase2/` 中完成测试、Build 和 Validate，但没有在 20 页内完整展开 P2 的 Build 参数。下面是**根据 PPT 的 P1 提交命令和两阶段规则写出的对应示例，执行前请用本期 `--help` 或课程手册核对 `--phase P2` 是否被接受**：

```bash
bin/datac submission build \
  --input /workspace/phase2 \
  --student-id 20250001 \
  --phase P2
```

### 第 5 步：验证真正生成的 P2 包

同样先从真实 Build 输出中取得产物路径，再验证：

```bash
bin/datac submission validate \
  --input /把这里换成Build实际输出的P2文件路径 \
  --phase P2
```

**不要把占位路径原样执行。** 如帮助和当前 Release 显示参数与上述示例不同，以现有版本公开接口为准。

按照课程冻结的提交结构，P2 逻辑总包应有五个一级文件：

```text
bigdata_p2_20250001.tar.gz
├── Tasks_20250001.tar.gz
├── Agent_20250001.tar.gz
├── metadata.json
├── review-comments.json
└── 实验报告模板.docx
```

实际文件名和后缀需与当前 Release 的正式提交协议一致。

### 第 6 步：提交并保存备份

只有当最终 P2 包 Validate 通过后，才把**这同一份包**提交到教师指定的平台。自己另存一份原样备份，留存 Build/Validate 的结果和必要的实验日志。

> **本节完成标准**：Phase 2 Reference 与 Agent 已运行；P2 最终包已 Build、Validate，正式提交的是验证过的同一文件。

---

## 18. 常见问题和分层排错

遇到异常时，按 **环境 → 文件 → Reference → Agent → Build/Exposure** 顺序检查，先确定是哪一层的问题，不要动不动就重装环境。

| 现象 | 首先在哪个终端检查 | 建议检查步骤 |
|---|---|---|
| `wsl` 不是有效命令 | Windows PowerShell | 核对 Windows 版本、是否管理员、WSL 是否安装；参见第 4 节 |
| WSL 安装后不是 VERSION 2 | Windows PowerShell | `wsl -l -v`，核对发行版及 WSL 版本 |
| `systemctl` 提示未以 systemd 启动 | Ubuntu | `ps -p 1 -o comm=`，核对 `/etc/wsl.conf`，PowerShell 执行 `wsl --shutdown` |
| `docker: command not found` | Ubuntu | 检查 Docker Engine 是否安装在 **WSL Ubuntu**，而不是只安装 Windows 客户端 |
| `Cannot connect to Docker daemon` | Ubuntu | `sudo systemctl is-active docker`、`sudo docker ps`，判断是服务还是权限问题 |
| `docker compose` 不识别 | Ubuntu | `docker compose version`，确认安装的是 Compose v2 插件 |
| `scripts/install.sh` 找不到 | Ubuntu | `pwd`、`ls scripts/`，检查是否进入正确 Release 根目录 |
| `bin/datac` 找不到 | 课程 CLI | `pwd`、`ls -l bin/datac`，核对课程启动入口，不要随意替换版本 |
| doctor 未通过 | 课程 CLI | 记录具体失败项，回到对应 WSL/Docker/Workspace/API 章节 |
| API 连接失败 | 课程 CLI | 核对生效的 `.env`、教师网关、网络和 Key，日志必须脱敏 |
| Reference 报缺列错误 | 课程 CLI | 检查 `data.csv` 列名与 `reference.py` 读取逻辑 |
| Reference 成功但 Answer 不一致 | 课程 CLI | 检查题意、数据、Reference 算法及 `task.json.answer` |
| Agent 运行成功但答案错 | 课程 CLI | 优先分析题意、列名、筛选、聚合、精度和输出格式，**不是重装 Docker** |
| Agent 运行异常 | 课程 CLI | 看 Python 错误、Agent 接口、API、超时和资源限制 |
| P1 Build 校验失败 | 课程 CLI | 看 `phase1/` 必需文件、metadata 学号及 schema、Reference 一致性 |
| P1 Validate 失败 | 课程 CLI | 检查传入的是不是 Build 真正生成的包；不要手动重压缩 |
| Exposure 导入后不足 10 个 | 课程 CLI | 查看 `exposure status`、任务包路径和分配清单；联系助教 |
| Exposure 运行影响本人 Task | 课程 CLI | 停止不规范的手工解压覆盖，保留证据并联系助教，不要继续覆盖后自行修复 |
| P2 Review 不完整 | 课程 CLI/编辑器 | 核对每个 AT ID、格式与课程模板 |

### 18.1 最小自检顺序

**[Ubuntu，在 Release 根目录]**

```bash
docker compose ps
```

**[课程 CLI]**

```bash
bin/datac --help
bin/datac doctor --workspace /workspace
```

如果环境正常但结果错了，接着运行对应 Phase 的 Reference 与 Agent 命令，找出是标准答案错误、Agent 解题错误还是运行异常。

### 18.2 求助助教时最好提供哪些信息？

```text
Release 版本：
操作系统 / WSL / Docker 版本：
在哪个终端操作：
正在执行哪个阶段：P1 / Exposure / P2
完整执行命令（不要附带秘密 Key）：
最后一段报错信息（已脱敏）：
预期发生什么：
实际上发生什么：
已尝试过哪些排查：
```

不要发送完整 `.env` 或含真实 `LLM_API_KEY` 的截图。

---

## 19. 阶段完成检查表

以下清单建议在**每次正式提交前**逐项勾选。

### 19.1 环境验收

- [ ] Windows 与虚拟化条件符合课程要求。
- [ ] `wsl -l -v` 显示 Ubuntu-22.04 / WSL2。
- [ ] Ubuntu PID 1 为 systemd。
- [ ] Docker Engine 服务正常，Compose v2 可用。
- [ ] 当前 Release 的课程容器已启动。
- [ ] `.env` 已配置，密钥没有泄漏。
- [ ] `bin/datac --help` 正常。
- [ ] `bin/datac doctor --workspace /workspace` 关键检查通过。

### 19.2 Phase 1 提交前

- [ ] `phase1/metadata.json` 已填真实学号、Task/Agent ID。
- [ ] `phase1/task.json` 的题意、输出规则明确。
- [ ] `phase1/data.csv` 支持题目要求。
- [ ] `phase1/reference.py` 能计算正确答案。
- [ ] `task.json.answer` 与 Reference 的真实结果一致。
- [ ] `phase1/agent.py` 使用课程正式接口，已实际运行。
- [ ] 已执行 P1 Build。
- [ ] 已用 **最终 P1 包**执行 Validate。
- [ ] 已保存并提交同一份验证通过的包。

### 19.3 Exposure 结束前

- [ ] 从助教获得分配给自己的 Exposure 包。
- [ ] 使用 `exposure import` 正式导入，没有覆盖自己的 P1。
- [ ] `exposure status` 显示应有的 10 个任务。
- [ ] 用 **Agent v1** 跑完匿名任务。
- [ ] 记录每题的运行结果和可核验的诊断证据。
- [ ] 按正式模板准备 10 个 Review。

### 19.4 Phase 2 提交前

- [ ] `phase2/` 的七个核心文件完整。
- [ ] Task v2 / Agent v2 的改动有理由；不改也有理由。
- [ ] `phase2/metadata.json` 已核对学生、阶段、Task/Agent/Review ID。
- [ ] `review-comments.json` 记录齐全、AT ID 无漏项。
- [ ] 实验报告模板已完成。
- [ ] 已执行 `reference run --phase phase2`。
- [ ] 已执行 `run --phase phase2`。
- [ ] 已执行 P2 Build 并找到实际生成的文件。
- [ ] 已用 **最终 P2 包**执行 Validate。
- [ ] 已提交同一份验证通过的包，并自留备份。

---

## 20. 全部常用命令速查

**请按下面顺序执行。命令环境比记住命令本身更重要。** 带有“示例路径/示例学号”的内容必须换成真实值。

### 20.1 Windows PowerShell（管理员）

```powershell
wsl --update
wsl --list --online
wsl --install -d Ubuntu-22.04
wsl -l -v
wsl -d Ubuntu-22.04
```

systemd 配置发生变化后，才需要：

```powershell
wsl --shutdown
```

### 20.2 Ubuntu（WSL）

```bash
whoami
cat /etc/os-release
ps -p 1 -o comm=
sudo apt update
sudo systemctl enable --now docker
sudo systemctl is-active docker
sudo docker version
sudo docker compose version
sudo docker ps
```

进入 Release 根目录（仅示例路径）：

```bash
cd /mnt/d/Course/BigData-v10.1.18
bash scripts/install.sh
docker compose ps
```

### 20.3 课程 CLI：环境检查

```bash
bin/datac --help
bin/datac doctor --workspace /workspace
```

### 20.4 课程 CLI：P1

```bash
bin/datac reference run --phase phase1
bin/datac run --phase phase1

bin/datac submission build \
  --input /workspace/phase1 \
  --student-id 20250001 \
  --phase P1

bin/datac submission validate \
  --input /替换成真实P1输出包路径 \
  --phase P1
```

### 20.5 课程 CLI：Exposure

```bash
bin/datac exposure import \
  --package /替换成教师发放的真实Exposure包路径 \
  --workspace /workspace

bin/datac exposure status --workspace /workspace
bin/datac exposure run --workspace /workspace
bin/datac exposure status --workspace /workspace
```

### 20.6 课程 CLI：P2

```bash
bin/datac reference run --phase phase2
bin/datac run --phase phase2
```

下面 P2 提交命令按 P1 同构编写；实际执行前核对本期 Release 的命令帮助：

```bash
bin/datac submission build \
  --input /workspace/phase2 \
  --student-id 20250001 \
  --phase P2

bin/datac submission validate \
  --input /替换成真实P2输出包路径 \
  --phase P2
```

### 20.7 一句话记住完整实践顺序

```text
WSL2 → Ubuntu → systemd → Docker → 课程 Release → .env → doctor
  → P1 metadata / Task / Agent
  → reference run (P1) → run (P1) → Build / Validate / Submit P1
  → exposure import → status → run → 10 Task Reviews
  → P2 Task / Agent / Review / Report
  → reference run (P2) → run (P2) → Build / Validate / Submit P2
```

---

## 21. 附录：Failure Mode 与官方参考文档

### 21.1 Failure Mode Key 的使用原则

按后续课程协议，`hypothesized_failure_mode` 是 Task 作者的**事前失败假设**，不等于实际运行后的失败类型；它应作为提交管理元数据保存，而不应作为 Agent 可见的题目内容泄漏。

如果当前版本沿用下列 canonical Key，填写时必须使用**完整英文标识、无序号**：

```text
FM_REQUIREMENT
FM_SCHEMA
FM_DATA_TYPE
FM_FILTER_SORT
FM_JOIN
FM_AGGREGATION
FM_TIME_TIMEZONE
FM_MISSING_VALUE
FM_OUTLIER_BOUNDARY
FM_DUPLICATE_RECORD_HANDLING
FM_PLANNING_DECOMPOSITION
FM_VERIFICATION
FM_NUMERICAL_PRECISION_ROUNDING
FM_PROMPT_INJECTION
```

禁止用旧编号格式，例如 `FM01_REQUIREMENT`、`FM_06_AGGREGATION`。若本期 Release 的模板和合法枚举与上述不同，应暂停提交并向助教核实，不要盲目更改字段来尝试绕过系统验证。

### 21.2 为什么 Agent 不应该直接看到标准答案？

课程要测的是 Agent 根据任务要求和数据**独立求解**的能力。即使 Task 提交包中保存了 `reference.py` 或 `answer` 供质量验证使用，也不代表 Agent 可以在求解中读取这些内容。

你应使用统一 `bin/datac run` 或 `bin/datac exposure run`，不要自己把标准答案塞进 Agent 的输入或直接抄录 Reference 的结果。

### 21.3 官方系统文档（安装排错用）

- [微软：安装 WSL（简体中文）](https://learn.microsoft.com/zh-cn/windows/wsl/install)
- [微软：在 WSL 中启用 systemd（简体中文）](https://learn.microsoft.com/zh-cn/windows/wsl/systemd)
- [微软：WSL 配置 / wsl.conf](https://learn.microsoft.com/zh-cn/windows/wsl/wsl-config)
- [Docker：Ubuntu 安装 Docker Engine](https://docs.docker.com/engine/install/ubuntu/)
- [Docker：在 Linux 上安装 Compose 插件](https://docs.docker.com/compose/install/linux/)

这些链接用于排查基础环境问题。**课程安装包、课程 API、镜像版本和 `datac` 接口仍以本学期教师发放的内容为准**。

### 21.4 本文内容与 20 页课堂 PPT 的对应关系

| PPT 页码 | PPT 主题 | 本文对应章节 |
|---|---|---|
| 1–3 | 课程目标、环境层次 | 1–2 |
| 4–5 | 安装前检查、WSL2 | 3–4 |
| 6–7 | systemd、Docker | 5–6 |
| 8–10 | 离线课程包、API、CLI、doctor | 7–9 |
| 11–13 | Workspace、Metadata、Task、Agent | 10–11 |
| 14–16 | Reference、Agent、Build、Validate、结果分析 | 12–13、18 |
| 17 | 匿名 Exposure | 14 |
| 18 | Phase 2 改进、Review、报告 | 15–17 |
| 19–20 | 排错、整体速查 | 18–20 |

---

## 版本维护记录

| 版本 | 日期 | 变更内容 | 维护责任 |
|---|---|---|---|
| V1.0 | 2026-10-14 | 依据 20 页 PPT 编写从安装到 P1 / Exposure / P2 的学生逐步操作指南，补充安装命令、阶段检查点、错误排查和速查表。 | 课程团队 |

**学期复用时建议检查**：当期 Release 版本、`scripts/install.sh` 是否存在、`bin/datac --help` 是否变化、P1/P2 Build 与 Validate 命令、Exposure 包格式、工作区结构、Metadata Schema、Review Schema、模型策略和 API 获取说明。若有变化，应更新这一份手册并记录版本，避免同时发放互相矛盾的旧版操作说明。
