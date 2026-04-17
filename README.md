# EZqqmanager

本项目用于在本地电脑上定时发送 QQ 群消息：
- 数据源：飞书多维表格（Bitable）
- 执行端：本地 NapCat
- 调度方式：每分钟轮询一次飞书，命中任务即发送

## 你要准备什么

1. Python 3.10+
2. 可正常运行的 NapCat（放在项目 `napcat/` 目录）
3. 飞书应用凭证（`App ID`、`App Secret`）
4. 飞书多维表格的 `app_token` 和 `table_id`

---

## 三步快启（推荐）

### 1. 安装依赖

```powershell
cd D:\MyProject\EZqqmanager
pip install -r requirements.txt
```

### 2. 生成配置

如果没有 `.env`，启动器会自动从 `.env.example` 生成。
你也可以手动复制：

```powershell
Copy-Item .env.example .env
```

### 3. 启动向导

```powershell
python launcher.py
```

启动后菜单：
- `1` 一键启动（推荐）
- `2` 配置向导
- `3` 运行自检（doctor）
- `4` 退出

建议首次先 `2` 填写配置，再 `3` 自检，最后 `1` 一键启动。

---

## 配置项说明（最少必填）

`.env` 最少需要这 4 项：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BITABLE_APP_TOKEN`
- `FEISHU_TABLE_ID`

其余项都可先保持默认。

---

## 自检能力（重点）

`launcher.py` 的 doctor 会检查：
- 配置是否缺失/占位符
- 飞书鉴权是否通过
- 多维表格是否可访问
- NapCat 文件是否齐全
- NapCat API 是否连通（`/get_login_info`）

命令行单独执行：

```powershell
python launcher.py --doctor
```

仅运行配置向导：

```powershell
python launcher.py --setup
```

---

## 运行主程序

一键启动会在自检通过后自动拉起 `main.py`。
你也可以手动运行：

```powershell
python main.py
```

---

## 常见问题

### 1) 飞书鉴权失败 `code=10003 invalid param`

通常是 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 错误或带了多余字符（空格、引号、占位符）。

### 2) NapCat 端口不通

确认：
- NapCat 已启动
- QQ 未崩溃
- `NAPCAT_API_BASE` 端口与你实际 OneBot 端口一致

### 3) 发送状态回写失败（403）

是飞书权限问题：给应用补齐多维表格读写权限并重新授权。

---

## 安全提示

- `.env` 包含敏感信息，不要上传到 GitHub。
- `.env.example` 只放占位符。

