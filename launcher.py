import argparse
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import requests
from dotenv import dotenv_values, load_dotenv, set_key

BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
ENV_EXAMPLE_PATH = BASE_DIR / ".env.example"
NAPCAT_DIR = BASE_DIR / "napcat"
NAPCAT_START_BAT = NAPCAT_DIR / "start.bat"
MAIN_SCRIPT = BASE_DIR / "main.py"

DEFAULTS: Dict[str, str] = {
    "FEISHU_API_BASE": "https://open.feishu.cn",
    "NAPCAT_API_BASE": "http://127.0.0.1:3000",
    "NAPCAT_API_PATH": "/send_group_msg",
    "POLL_INTERVAL_MINUTES": "1",
    "FIELD_GROUP_ID": "群号",
    "FIELD_CONTENT": "公告内容",
    "FIELD_IMAGE": "公告图片链接",
    "FIELD_PLAN_TIME": "计划发送时间",
    "FIELD_STATUS": "执行状态",
}

PLACEHOLDER_VALUES = {
    "your_app_id",
    "your_app_secret",
    "your_bitable_app_token",
    "your_table_id",
    "replace_me",
    "changeme",
}

REQUIRED_KEYS = [
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BITABLE_APP_TOKEN",
    "FEISHU_TABLE_ID",
]


@dataclass
class CheckResult:
    ok: bool
    title: str
    detail: str


def print_header() -> None:
    print("=" * 64)
    print("EZqqmanager 启动器（向导 + 自检 + 一键启动）")
    print("=" * 64)
    print(f"项目目录: {BASE_DIR}")
    print(f"配置文件: {ENV_PATH}")


def ensure_env_file() -> None:
    if ENV_PATH.exists():
        return
    if ENV_EXAMPLE_PATH.exists():
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print("[提示] 未发现 .env，已从 .env.example 自动创建。")
        return
    ENV_PATH.write_text("", encoding="utf-8")
    print("[提示] 未发现 .env/.env.example，已创建空 .env。")


def load_env() -> Dict[str, str]:
    raw = dotenv_values(ENV_PATH)
    data: Dict[str, str] = {}
    for key, value in raw.items():
        data[key] = (value or "").strip().strip('"').strip("'")
    for key, value in DEFAULTS.items():
        if not data.get(key):
            data[key] = value
    return data


def save_env_value(key: str, value: str) -> None:
    set_key(str(ENV_PATH), key, value, quote_mode="never")


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:4]}***{value[-2:]}"


def is_placeholder(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return True
    return text in PLACEHOLDER_VALUES or text.startswith("your_")


def find_qq_path(current: str) -> str:
    if current and Path(current).exists():
        return current
    candidates = [
        r"D:\QQNT\QQ.exe",
        r"C:\Program Files\Tencent\QQNT\QQ.exe",
        r"C:\Program Files (x86)\Tencent\QQNT\QQ.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return current or r"D:\QQNT\QQ.exe"


def prompt_value(label: str, current: str, required: bool = True) -> str:
    while True:
        hint = f"[{current}]" if current else "[未设置]"
        text = input(f"{label} {hint}: ").strip()
        value = text if text else current
        value = value.strip()
        if value or not required:
            return value
        print("  该项不能为空，请重新输入。")


def run_setup_wizard(env: Dict[str, str]) -> Dict[str, str]:
    print("\n--- 配置向导 ---")
    print("按回车可保留当前值。\n")

    env["FEISHU_APP_ID"] = prompt_value("FEISHU_APP_ID", env.get("FEISHU_APP_ID", ""))
    env["FEISHU_APP_SECRET"] = prompt_value("FEISHU_APP_SECRET", env.get("FEISHU_APP_SECRET", ""))
    env["FEISHU_BITABLE_APP_TOKEN"] = prompt_value("FEISHU_BITABLE_APP_TOKEN", env.get("FEISHU_BITABLE_APP_TOKEN", ""))
    env["FEISHU_TABLE_ID"] = prompt_value("FEISHU_TABLE_ID", env.get("FEISHU_TABLE_ID", ""))

    suggested_qq = find_qq_path(env.get("QQ_PATH", ""))
    env["QQ_PATH"] = prompt_value("QQ_PATH", suggested_qq)

    for key, value in DEFAULTS.items():
        if not env.get(key):
            env[key] = value

    print("\n写入 .env ...")
    for key, value in env.items():
        save_env_value(key, value)
    load_dotenv(ENV_PATH, override=True)

    print("配置已保存。")
    print(f"  FEISHU_APP_ID: {mask(env.get('FEISHU_APP_ID', ''))}")
    print(f"  FEISHU_APP_SECRET长度: {len(env.get('FEISHU_APP_SECRET', ''))}")
    print(f"  QQ_PATH: {env.get('QQ_PATH', '')}")
    return env


def wait_for_port(host: str, port: int, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def check_required_env(env: Dict[str, str]) -> CheckResult:
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        return CheckResult(False, "配置完整性", f"缺少必填项: {', '.join(missing)}")

    placeholder = [key for key in REQUIRED_KEYS if is_placeholder(env.get(key, ""))]
    if placeholder:
        return CheckResult(False, "配置完整性", f"仍是示例占位符: {', '.join(placeholder)}")

    if not env.get("FEISHU_APP_ID", "").startswith("cli_"):
        return CheckResult(False, "配置完整性", "FEISHU_APP_ID 格式异常，通常应以 cli_ 开头")

    qq_path = env.get("QQ_PATH", "")
    if not qq_path:
        return CheckResult(False, "配置完整性", "QQ_PATH 未设置")
    if not Path(qq_path).exists():
        return CheckResult(False, "配置完整性", f"QQ_PATH 不存在: {qq_path}")

    return CheckResult(True, "配置完整性", "通过")


def check_feishu_auth(env: Dict[str, str]) -> Tuple[CheckResult, str]:
    url = env.get("FEISHU_API_BASE", DEFAULTS["FEISHU_API_BASE"]).rstrip("/") + "/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": env.get("FEISHU_APP_ID", ""),
        "app_secret": env.get("FEISHU_APP_SECRET", ""),
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
    except requests.exceptions.RequestException as exc:
        return CheckResult(False, "飞书鉴权", f"网络错误: {exc}"), ""

    try:
        data = resp.json()
    except ValueError:
        return CheckResult(False, "飞书鉴权", f"返回非 JSON，HTTP={resp.status_code}"), ""

    if resp.status_code != 200:
        return CheckResult(False, "飞书鉴权", f"HTTP={resp.status_code}, body={str(data)[:200]}"), ""

    if data.get("code") != 0:
        return (
            CheckResult(
                False,
                "飞书鉴权",
                f"code={data.get('code')} msg={data.get('msg')}（请核对 App ID/Secret）",
            ),
            "",
        )

    token = data.get("tenant_access_token", "")
    expire = data.get("expire", "?")
    return CheckResult(True, "飞书鉴权", f"通过（expire={expire}s）"), token


def check_bitable_access(env: Dict[str, str], token: str) -> CheckResult:
    if not token:
        return CheckResult(False, "飞书表格访问", "缺少 token，已跳过")

    base = env.get("FEISHU_API_BASE", DEFAULTS["FEISHU_API_BASE"]).rstrip("/")
    app_token = env.get("FEISHU_BITABLE_APP_TOKEN", "")
    table_id = env.get("FEISHU_TABLE_ID", "")
    url = f"{base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        resp = requests.get(url, headers=headers, params={"page_size": 1}, timeout=10)
    except requests.exceptions.RequestException as exc:
        return CheckResult(False, "飞书表格访问", f"网络错误: {exc}")

    try:
        data = resp.json()
    except ValueError:
        return CheckResult(False, "飞书表格访问", f"返回非 JSON，HTTP={resp.status_code}")

    if resp.status_code != 200:
        return CheckResult(False, "飞书表格访问", f"HTTP={resp.status_code}, body={str(data)[:200]}")

    if data.get("code") != 0:
        return CheckResult(False, "飞书表格访问", f"code={data.get('code')} msg={data.get('msg')}")

    return CheckResult(True, "飞书表格访问", "通过")


def check_napcat_files() -> CheckResult:
    if not NAPCAT_DIR.exists():
        return CheckResult(False, "NapCat 文件", f"目录不存在: {NAPCAT_DIR}")
    required = [NAPCAT_START_BAT, NAPCAT_DIR / "napcat.mjs", NAPCAT_DIR / "NapCatWinBootMain.exe"]
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        return CheckResult(False, "NapCat 文件", "缺少文件: " + "; ".join(missing))
    return CheckResult(True, "NapCat 文件", "通过")


def check_napcat_api(api_base: str) -> CheckResult:
    url = api_base.rstrip("/") + "/get_login_info"
    try:
        resp = requests.post(url, json={}, timeout=5)
    except requests.exceptions.RequestException as exc:
        return CheckResult(False, "NapCat API", f"未连通: {exc}")

    if resp.status_code != 200:
        return CheckResult(False, "NapCat API", f"HTTP={resp.status_code}, body={resp.text[:180]}")

    body = (resp.text or "").strip()
    if not body:
        return CheckResult(False, "NapCat API", "HTTP 200 但返回体为空")

    return CheckResult(True, "NapCat API", "连通")


def print_results(results: list[CheckResult]) -> bool:
    print("\n--- 自检结果 ---")
    all_ok = True
    for item in results:
        flag = "[OK]" if item.ok else "[FAIL]"
        print(f"{flag:<7} {item.title}: {item.detail}")
        if not item.ok:
            all_ok = False
    print("----------------")
    return all_ok


def run_doctor(env: Dict[str, str], include_napcat_api: bool = True) -> Tuple[bool, str]:
    results: list[CheckResult] = []

    cfg_result = check_required_env(env)
    results.append(cfg_result)
    token = ""

    if cfg_result.ok:
        auth_result, token = check_feishu_auth(env)
        results.append(auth_result)
        if auth_result.ok:
            results.append(check_bitable_access(env, token))

    results.append(check_napcat_files())

    if include_napcat_api:
        api_base = env.get("NAPCAT_API_BASE", DEFAULTS["NAPCAT_API_BASE"])
        results.append(check_napcat_api(api_base))

    ok = print_results(results)
    return ok, token


def choose_login_mode() -> str:
    print("\n请选择登录方式:")
    print("  1. 快速登录（已登录过的账号）")
    print("  2. 账号密码登录（如你已配置QQ_ACCOUNT/QQ_PASSWORD）")
    print("  3. 扫码登录（推荐）")
    while True:
        choice = input("输入 1/2/3: ").strip()
        if choice in {"1", "2", "3"}:
            return choice
        print("请输入 1、2 或 3")


def start_napcat(env: Dict[str, str], mode: str) -> bool:
    qq_path = env.get("QQ_PATH", "")
    api_base = env.get("NAPCAT_API_BASE", DEFAULTS["NAPCAT_API_BASE"])
    host = "127.0.0.1"
    port = 3000
    try:
        port = int(api_base.rsplit(":", 1)[1])
    except Exception:
        pass

    runtime_env = os.environ.copy()
    runtime_env["QQ_PATH"] = qq_path
    if mode == "1" and env.get("QQ_ACCOUNT"):
        runtime_env["NAPCAT_QUICK_ACCOUNT"] = env["QQ_ACCOUNT"]
    if mode == "2":
        if env.get("QQ_ACCOUNT"):
            runtime_env["NAPCAT_QUICK_ACCOUNT"] = env["QQ_ACCOUNT"]
        if env.get("QQ_PASSWORD"):
            runtime_env["NAPCAT_QUICK_PASSWORD"] = env["QQ_PASSWORD"]

    load_js = NAPCAT_DIR / "loadNapCat.js"
    napcat_mjs = NAPCAT_DIR / "napcat.mjs"
    load_js.write_text(f"(async () => {{await import('file:///{napcat_mjs.as_posix()}')}})()\n", encoding="utf-8")

    print("\n启动 NapCat ...")
    subprocess.Popen(["cmd", "/c", str(NAPCAT_START_BAT)], cwd=str(NAPCAT_DIR), env=runtime_env)

    print("等待 NapCat OneBot 接口就绪（最多 120 秒）...")
    if not wait_for_port(host, port, timeout=120):
        print("[失败] 120 秒内未等到端口监听。")
        print("建议检查：QQ 是否崩溃、NapCat 版本是否匹配、是否被安全软件拦截。")
        return False

    api_result = check_napcat_api(api_base)
    if not api_result.ok:
        print(f"[警告] 端口已开但未登录完成：{api_result.detail}")
        if mode == "3":
            qr_path = NAPCAT_DIR / "cache" / "qrcode.png"
            print(f"请扫码登录。二维码路径（若存在）：{qr_path}")
        print("你可以继续等待登录完成后再启动主程序。")
        return False

    print("NapCat 已就绪。")
    return True


def start_main() -> int:
    if not MAIN_SCRIPT.exists():
        print(f"[失败] 主程序不存在: {MAIN_SCRIPT}")
        return 1
    print("\n启动主守护程序 main.py ...\n")
    return subprocess.call([sys.executable, str(MAIN_SCRIPT)], cwd=str(BASE_DIR))


def one_click_start() -> int:
    ensure_env_file()
    load_dotenv(ENV_PATH, override=True)
    env = load_env()

    cfg_result = check_required_env(env)
    if not cfg_result.ok:
        print(f"\n[提示] 当前配置不完整：{cfg_result.detail}")
        answer = input("是否现在进入配置向导？(Y/n): ").strip().lower()
        if answer not in {"", "y", "yes"}:
            return 1
        env = run_setup_wizard(env)

    print("\n先做启动前自检...")
    ok, _ = run_doctor(env, include_napcat_api=False)
    if not ok:
        print("\n自检未通过，请修复后重试。")
        return 1

    api_base = env.get("NAPCAT_API_BASE", DEFAULTS["NAPCAT_API_BASE"])
    api_result = check_napcat_api(api_base)
    if not api_result.ok:
        mode = choose_login_mode()
        if not start_napcat(env, mode):
            print("\nNapCat 尚未完成登录，暂不启动主程序。")
            return 1
    else:
        print("\n检测到 NapCat 已在线，跳过登录步骤。")

    return start_main()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EZqqmanager launcher")
    parser.add_argument("--doctor", action="store_true", help="仅运行启动前自检")
    parser.add_argument("--setup", action="store_true", help="仅运行配置向导")
    return parser.parse_args()


def menu_loop() -> int:
    while True:
        print("\n请选择操作:")
        print("  1. 一键启动（推荐）")
        print("  2. 配置向导")
        print("  3. 运行自检")
        print("  4. 退出")
        choice = input("输入 1/2/3/4: ").strip()

        ensure_env_file()
        load_dotenv(ENV_PATH, override=True)
        env = load_env()

        if choice == "1":
            return one_click_start()
        if choice == "2":
            run_setup_wizard(env)
            continue
        if choice == "3":
            run_doctor(env, include_napcat_api=True)
            continue
        if choice == "4":
            return 0

        print("请输入 1、2、3 或 4")


def main() -> int:
    print_header()
    args = parse_args()

    ensure_env_file()
    load_dotenv(ENV_PATH, override=True)
    env = load_env()

    if args.setup:
        run_setup_wizard(env)
        return 0

    if args.doctor:
        ok, _ = run_doctor(env, include_napcat_api=True)
        return 0 if ok else 1

    return menu_loop()


if __name__ == "__main__":
    sys.exit(main())
