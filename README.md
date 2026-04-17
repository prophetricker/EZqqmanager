# EZqqmanager 使用说明

自动定时发送 QQ 群消息的工具。在飞书多维表格里填好消息内容和发送时间，程序会自动在指定时间把消息发到 QQ 群，并回写发送状态。

---

## 一、环境准备

### 1. 安装 Python

1. 打开 https://www.python.org/downloads/
2. 点击 **Download Python 3.x.x**（选 3.10 或更高版本）
3. 运行安装包，**务必勾选 "Add Python to PATH"**，然后点 Install Now
4. 安装完成后，按 `Win+R`，输入 `cmd`，回车，输入 `python --version`，能看到版本号即成功

### 2. 确认 QQ 版本

本工具需要 QQ **9.9.28（版本号 46494）**。

- 打开 QQ → 左上角头像 → 关于 QQ，查看版本号
- 如果版本不符，请先卸载当前 QQ，再安装对应版本

### 3. 确认 QQ 安装路径

默认路径是 `D:\QQNT\QQ.exe`。如果你的 QQ 装在其他位置，记下完整路径（后面配置会用到）。

### 4. 安装 NapCat

NapCat 是本工具的 QQ 消息发送后端，需要单独下载安装。

1. 打开 https://github.com/NapNeko/NapCatQQ/releases，下载最新版 `NapCat.Shell.Windows.OneKey.zip`
2. 解压后运行 `NapCatInstaller.exe`，按提示完成安装
3. 安装完成后，将 `NapCat.Shell` 目录下的所有文件复制到本项目的 `napcat\` 文件夹中

---

## 二、首次配置

### 1. 复制配置文件

在项目文件夹里找到 `.env.example`，复制一份，重命名为 `.env`。

> 提示：`.env` 文件默认是隐藏的，如果看不到，在文件夹里点 **查看 → 显示隐藏的项目**。

### 2. 用记事本打开 `.env`，填写以下内容

```
# QQ 路径（改成你电脑上 QQ.exe 的完整路径）
QQ_PATH=D:\QQNT\QQ.exe

# 飞书应用配置（从飞书开放平台获取）
FEISHU_APP_ID=你的AppID
FEISHU_APP_SECRET=你的AppSecret
FEISHU_BITABLE_APP_TOKEN=多维表格的AppToken
FEISHU_TABLE_ID=表格ID

# 以下保持默认即可
FEISHU_API_BASE=https://open.feishu.cn
NAPCAT_API_BASE=http://127.0.0.1:3000
NAPCAT_API_PATH=/send_group_msg
POLL_INTERVAL_MINUTES=1
FIELD_GROUP_ID=群号
FIELD_CONTENT=公告内容
FIELD_IMAGE=公告图片链接
FIELD_PLAN_TIME=计划发送时间
FIELD_STATUS=执行状态
```

填完后保存。

---

## 三、安装依赖

1. 在项目文件夹的空白处，按住 `Shift` 键右键，选择 **在此处打开命令窗口**（或 PowerShell）
2. 输入以下命令，回车：

```
pip install -r requirements.txt
```

等待安装完成（看到 Successfully installed 即可）。

---

## 四、启动步骤

### 第一步：启动 NapCat（QQ 后台服务）

1. 进入项目文件夹下的 `napcat` 子文件夹
2. 右键 `start.bat` → **以管理员身份运行**
3. 首次运行会显示一个二维码，用**手机 QQ** 扫码登录
4. 看到 `WebUi User Panel Url: http://127.0.0.1:6099/webui` 说明启动成功
5. **保持这个窗口开着，不要关闭**

### 第二步：启动主程序

1. 回到项目根目录
2. 双击 `main.py`，或在命令窗口输入：

```
python main.py
```

3. 看到 `开始轮询` 字样即表示运行正常

---

## 五、日常使用

程序运行后，只需在飞书多维表格里操作：

| 字段 | 说明 |
|------|------|
| 群号 | QQ 群的群号（纯数字） |
| 公告内容 | 要发送的文字内容 |
| 公告图片链接 | 可选，填图片 URL |
| 计划发送时间 | 指定发送时间 |
| 执行状态 | 填 `待发送`，发送后自动改为 `已发送` |

---

## 六、常见问题

**Q：start.bat 运行后立刻关闭？**
A：右键选"以管理员身份运行"，不要直接双击。

**Q：提示"文件已损坏，请重新安装QQ"？**
A：QQ 版本不对，需要安装 9.9.28（46494）版本。

**Q：扫码后提示登录失败？**
A：确保手机 QQ 和电脑 QQ 是同一个账号，重新运行 start.bat 获取新二维码。

**Q：main.py 报错找不到模块？**
A：重新执行 `pip install -r requirements.txt`。

**Q：消息没有发出去？**
A：确认 napcat 的 start.bat 窗口还开着，且端口 3000 正常（可在浏览器访问 `http://127.0.0.1:3000/get_login_info` 验证）。
