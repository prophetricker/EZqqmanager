import argparse
import json
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
NAPCAT_LAUNCHER_NAMES = (
    "start.bat",
    "launch.bat",
    "launcher-user.bat",
    "launcher.bat",
)
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


def wait_for_any_port(host: str, ports: list[int], timeout: int) -> bool:
    if not ports:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        for port in ports:
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError:
                continue
        time.sleep(1)
    return False


def parse_port_from_api_base(api_base: str) -> int | None:
    value = (api_base or "").strip().rstrip("/")
    if not value:
        return None
    host_part = value
    if "://" in host_part:
        host_part = host_part.split("://", 1)[1]
    host_part = host_part.split("/", 1)[0]
    if ":" not in host_part:
        return None
    port_text = host_part.rsplit(":", 1)[1]
    if not port_text.isdigit():
        return None
    return int(port_text)


def find_napcat_launcher() -> Path | None:
    for name in NAPCAT_LAUNCHER_NAMES:
        candidate = NAPCAT_DIR / name
        if candidate.exists():
            return candidate
    bat_files = sorted(NAPCAT_DIR.glob("*.bat"))
    if bat_files:
        return bat_files[0]
    return None


def discover_napcat_ports_from_config() -> list[int]:
    config_dir = NAPCAT_DIR / "config"
    if not config_dir.exists():
        return []

    ports: list[int] = []
    for file_path in sorted(config_dir.glob("onebot11*.json")):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        network = data.get("network", {})
        servers = network.get("httpServers", [])
        if not isinstance(servers, list):
            continue
        for item in servers:
            if not isinstance(item, dict):
                continue
            if item.get("enable") is False:
                continue
            port = item.get("port")
            if isinstance(port, int) and 1 <= port <= 65535 and port not in ports:
                ports.append(port)

    return ports


def build_napcat_api_bases(env: Dict[str, str]) -> list[str]:
    bases: list[str] = []

    def add(base: str) -> None:
        value = (base or "").strip().rstrip("/")
        if value and value not in bases:
            bases.append(value)

    add(env.get("NAPCAT_API_BASE", DEFAULTS["NAPCAT_API_BASE"]))

    for port in discover_napcat_ports_from_config():
        add(f"http://127.0.0.1:{port}")

    # fallback common OneBot ports
    for port in (3000, 3001):
        add(f"http://127.0.0.1:{port}")

    return bases


def normalize_api_base(api_base: str) -> str:
    return (api_base or "").strip().rstrip("/")


def maybe_update_napcat_api_base(env: Dict[str, str], detected_base: str) -> None:
    if not detected_base:
        return
    current = normalize_api_base(env.get("NAPCAT_API_BASE", DEFAULTS["NAPCAT_API_BASE"]))
    target = normalize_api_base(detected_base)
    if not target or current == target:
        return
    save_env_value("NAPCAT_API_BASE", target)
    env["NAPCAT_API_BASE"] = target
    print(f"[提示] 已自动更新 NAPCAT_API_BASE -> {target}")


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
        return CheckResult(False, "NapCat ??", f"?????: {NAPCAT_DIR}")

    launcher = find_napcat_launcher()
    if launcher is None:
        expected = ", ".join(NAPCAT_LAUNCHER_NAMES)
        return CheckResult(False, "NapCat ??", f"???????????: {expected}")

    runtime_markers = [
        NAPCAT_DIR / "napcat.mjs",
        NAPCAT_DIR / "NapCatWinBootMain.exe",
        NAPCAT_DIR / "package.json",
    ]
    if not any(item.exists() for item in runtime_markers):
        marker_names = ", ".join(item.name for item in runtime_markers)
        return CheckResult(False, "NapCat ??", f"?????????????: {marker_names}")

    return CheckResult(True, "NapCat ??", f"???????: {launcher.name}?")

def check_napcat_api(api_base: str) -> CheckResult:
    url = api_base.rstrip("/") + "/get_login_info"
    try:
        resp = requests.post(url, json={}, timeout=5)
    except requests.exceptions.RequestException as exc:
        return CheckResult(
            False,
            "NapCat API",
            f"{api_base.rstrip('/')} ????????? NapCat ?????: {exc}",
        )

    if resp.status_code != 200:
        return CheckResult(False, "NapCat API", f"HTTP={resp.status_code}, body={resp.text[:180]}")

    body = (resp.text or "").strip()
    if not body:
        return CheckResult(False, "NapCat API", "HTTP 200 ??????")

    return CheckResult(True, "NapCat API", f"???{api_base.rstrip('/')}?")

def check_napcat_api_candidates(env: Dict[str, str]) -> Tuple[CheckResult, str]:
    errors: list[str] = []
    for base in build_napcat_api_bases(env):
        result = check_napcat_api(base)
        if result.ok:
            return result, normalize_api_base(base)
        errors.append(f"{normalize_api_base(base)} -> {result.detail}")

    detail = " | ".join(errors[:3]) if errors else "???? API ????"
    return CheckResult(False, "NapCat API", detail), ""

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
        api_result, _ = check_napcat_api_candidates(env)
        results.append(api_result)

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
    if napcat_mjs.exists():
        load_js.write_text(f"(async () => {{await import('file:///{napcat_mjs.as_posix()}')}})()\n", encoding="utf-8")

    launcher = find_napcat_launcher()
    if launcher is None:
        expected = ", ".join(NAPCAT_LAUNCHER_NAMES)
        print(f"[??] ??? NapCat ?????????: {expected}")
        return False

    print("\n?? NapCat ...")
    subprocess.Popen(["cmd", "/c", str(launcher)], cwd=str(NAPCAT_DIR), env=runtime_env)

    api_bases = build_napcat_api_bases(env)
    ports = [p for p in (parse_port_from_api_base(base) for base in api_bases) if p]

    print("?? NapCat OneBot ??????? 120 ??...")
    if not wait_for_any_port("127.0.0.1", ports, timeout=120):
        print("[??] 120 ????? OneBot ?????")
        print("?????QQ ?????NapCat ?????????????????")
        return False

    api_result, detected_base = check_napcat_api_candidates(env)
    if not api_result.ok:
        print(f"[??] ????? API ?????{api_result.detail}")
        if mode == "3":
            qr_path = NAPCAT_DIR / "cache" / "qrcode.png"
            print(f"?????????????????{qr_path}")
        print("???????????????????")
        return False

    maybe_update_napcat_api_base(env, detected_base)
    print(f"NapCat ????{detected_base}??")
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
        print(f"\n[??] ????????{cfg_result.detail}")
        answer = input("???????????(Y/n): ").strip().lower()
        if answer not in {"", "y", "yes"}:
            return 1
        env = run_setup_wizard(env)

    print("\n???????...")
    ok, _ = run_doctor(env, include_napcat_api=False)
    if not ok:
        print("\n?????????????")
        return 1

    api_result, detected_base = check_napcat_api_candidates(env)
    if not api_result.ok:
        mode = choose_login_mode()
        if not start_napcat(env, mode):
            print("\nNapCat ???????????????")
            return 1
    else:
        maybe_update_napcat_api_base(env, detected_base)
        print(f"\n??? NapCat ????{detected_base}?????????")

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
