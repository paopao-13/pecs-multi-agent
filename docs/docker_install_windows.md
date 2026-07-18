# Windows 安装 Docker 傻瓜式教程（Route C 前置）

> 目标：在你的 Windows 电脑上装好 Docker Desktop，让 PECS 能跑起**真实 WebShop 环境**。
> 照着一步步做，遇到卡点看末尾「常见坑」。全程约 20-40 分钟（含下载）。

---

## 第 0 步：确认你的电脑够格

| 检查项 | 要求 | 怎么看 |
|---|---|---|
| 系统版本 | Win10 64位 21H2+ 或 Win11 | `Win+R` → 输入 `winver` 回车看版本号 |
| 内存 | 建议 ≥16GB（WebShop 跑起来吃内存） | `Ctrl+Shift+Esc` → 性能 → 内存 |
| 磁盘 | 剩余 ≥20GB（Docker + WebShop 镜像很大） | 此电脑 → 看 C 盘剩余 |
| 管理员权限 | 需要 | 你能在「开始」右键「以管理员身份运行」就行 |
| 虚拟化 | 必须在 BIOS/固件开启（VT-x/AMD-V） | 任务管理器 → 性能 → CPU → 看「虚拟化：已启用」 |

> 如果「虚拟化：已禁用」，需进 BIOS 开启（不同主板按键不同，常见 F2/Del，找
> "Intel Virtualization Technology" 或 "SVM Mode" 设为 Enabled）。这一步卡住
> 的人最多，值得先确认。

---

## 第 1 步：启用 WSL 2（推荐方式，比老式 Hyper-V 稳）

1. 右键「开始」菜单 → **「终端(管理员)」** 或 **「Windows PowerShell(管理员)」**
2. 粘贴下面这行，回车（需要联网，约 1-2 分钟自动装完并重启）：
   ```powershell
   wsl --install
   ```
3. 装完会提示重启，重启电脑。
4. 重启后第一次打开「终端」，会让你设一个 Linux 用户名和密码（随便设，记住即可）。

> 如果你只想用最省事的方式，做完这步就够了，Docker Desktop 会自己用 WSL2。

---

## 第 2 步：下载并安装 Docker Desktop

1. 打开浏览器访问：**https://www.docker.com/products/docker-desktop/**
2. 点 **「Download for Windows」**（下的是 `Docker Desktop Installer.exe`）
3. 双击安装，一路 Next，**关键勾选项**：
   - ✅ **Use WSL 2 instead of Hyper-V**（默认勾上就别动）
   - ✅ 其他选项默认即可
4. 点 Install，等进度条走完。
5. 安装完会提示 **Close and restart**（点它，电脑重启一次）。

---

## 第 3 步：启动并验证 Docker

1. 重启后，从开始菜单打开 **Docker Desktop**（第一次启动会初始化，等鲸鱼图标
   在状态栏**变绿/安静**才算就绪，约 10-30 秒）。
2. 右键「开始」→「终端(管理员)」，逐条验证：
   ```powershell
   docker --version
   ```
   应看到类似 `Docker version 27.x.x`（有版本号 = 装好了）。
3. 跑一个官方测试容器（验证能拉镜像、能运行）：
   ```powershell
   docker run hello-world
   ```
   第一次会下载一个小镜像（几十 MB），看到 `Hello from Docker!` 那段英文 = **成功**。

> 如果 `hello-world` 报错，看末尾「常见坑」第 4 条。

---

## 第 4 步：用 Docker 部署真实 WebShop 环境

Docker 装好后，在项目目录里一键启动（脚本会自己 clone 仓库、构建镜像、起容器）：

**方式 A（最简单，双击运行）：**
- 双击 `scripts/run_real_webshop.bat`，按提示等它跑完。

**方式 B（命令行）：**
```powershell
cd D:\简历\pecs-multi-agent
scripts\run_real_webshop.bat
```

跑完后，浏览器打开 **http://localhost:3000** 能看到购物网站 = 环境就绪。

---

## 第 5 步：让 PECS 连上真实环境并跑评测

在「终端(管理员)」里切到项目目录，设置环境变量后跑：

```powershell
cd D:\简历\pecs-multi-agent
set WEBSHOP_SERVER_URL=http://localhost:3000
python run_resumable.py webshop_001
```

只要 `WEBSHOP_SERVER_URL` 设了，PECS 的 `webshop` 工具就会自动改走真实环境
（多轮 search→click→buy），不再用本地 8 商品玩具。评测对比：

```powershell
python -c "from benchmarks.webshop_eval import evaluate_webshop, evaluate_react_webshop; import json; print(json.dumps(evaluate_webshop(), ensure_ascii=False, indent=2)); print(json.dumps(evaluate_react_webshop(), ensure_ascii=False, indent=2))"
```

两次成功率的差值，就是你要的 **WebShop +pp**（真实榜单版）。

---

## 常见坑（按出现频率排序）

### 1. 「虚拟化：已禁用」
→ 进 BIOS 开 VT-x / SVM Mode。笔记本常见快捷键 F2 / Fn+F2 / Del，开机瞬间狂按。

### 2. Docker 启动报「WSL 2 installation is incomplete」
→ 以管理员跑：`wsl --update` 然后 `wsl --set-default-version 2`。还不行就重装
WSL：`wsl --unregister docker-desktop`（谨慎）后重启 Docker。

### 3. `docker run hello-world` 卡在拉镜像 / 超时
→ 公司网/校园网可能墙了 Docker Hub。两种解法：
- 换手机热点试试；
- 或配置国内镜像加速：Docker Desktop → Settings → Docker Engine → 在配置里加
  `"registry-mirrors": ["https://docker.m.daocloud.io"]` → Apply & Restart。

### 4. 提示「Hyper-V / 虚拟机平台未启用」
→ 以管理员跑：
```powershell
dism.exe /online /enable-feature /featurename:Microsoft-Hyper-V /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```
然后**重启**。

### 5. WebShop 容器被系统杀掉 / 构建失败（内存不足）
→ WebShop 镜像很吃内存。Docker Desktop → Settings → Resources → 把 Memory
调到 **12-16GB**（机器总共 16G 就给 12G，32G 就给 16G+）。`mem_limit` 在
`docker-compose.yml` / `.bat` 里也同步调小。

### 6. 端口 3000 被占用
→ 关掉占用 3000 的程序（如某些开发服务器），或改 `docker-compose.yml` 的
`"3000:3000"` 为 `"3001:3000"`，并把 `WEBSHOP_SERVER_URL` 改为 `http://localhost:3001`。

---

## 排错 checklist

- [ ] `docker --version` 有输出
- [ ] `docker run hello-world` 看到 Hello from Docker
- [ ] Docker Desktop 状态栏鲸鱼图标**变绿**
- [ ] `http://localhost:3000` 能打开 WebShop 网站
- [ ] `WEBSHOP_SERVER_URL` 已 set（且是同一个终端窗口里跑评测）

全部打勾 = 可以开始真评测了。
