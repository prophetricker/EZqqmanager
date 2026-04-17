import os
import sys
import time
import subprocess
import socket
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

QQ_PATH = os.getenv("QQ_PATH", r"D:\QQNT\QQ.exe")
QQ_ACCOUNT = os.getenv("QQ_ACCOUNT", "")
QQ_PASSWORD = os.getenv("QQ_PASSWORD", "")
WEBUI_PORT = 6099  # NapCat 启动后立即监听
NAPCAT_PORT = int(os.getenv("NAPCAT_PORT", "3000"))  # 登录后才监听


def wait_for_port(port: int, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def choose_login_mode() -> str:
    print("请选择登录方式：")
    print("  1. 快速登录（已登录过的账号，无需扫码）")
    print("  2. 账号密码登录")
    print("  3. 扫二维码登录")
    while True:
        choice = input("请输入 1/2/3：").strip()
        if choice in ("1", "2", "3"):
            return choice
        print("请输入 1、2 或 3")


def start_napcat(mode: str):
    env = os.environ.copy()
    env["QQ_PATH"] = QQ_PATH

    if mode == "1":
        env["NAPCAT_QUICK_ACCOUNT"] = QQ_ACCOUNT
    elif mode == "2":
        env["NAPCAT_QUICK_ACCOUNT"] = QQ_ACCOUNT
        env["NAPCAT_QUICK_PASSWORD"] = QQ_PASSWORD
    # mode == "3": 不传账号，走二维码

    start_bat = str(BASE_DIR / "napcat" / "start.bat")
    subprocess.Popen(["cmd", "/c", start_bat], env=env, cwd=str(BASE_DIR / "napcat"))
    print("NapCat 启动中，等待服务就绪...")

    if not wait_for_port(WEBUI_PORT, timeout=30):
        print("错误：NapCat 未能在 30 秒内启动。")
        input("按回车退出...")
        sys.exit(1)

    if mode == "3":
        print(f"请扫描 napcat\\cache\\qrcode.png 中的二维码登录，然后等待...")

    print(f"等待 OneBot 服务就绪（端口 {NAPCAT_PORT}）...")
    if not wait_for_port(NAPCAT_PORT, timeout=120):
        print(f"错误：OneBot 服务未能在 120 秒内就绪，请确认已完成登录。")
        input("按回车退出...")
        sys.exit(1)
    print("NapCat 已就绪，启动主程序...")


def start_main():
    subprocess.run([sys.executable, str(BASE_DIR / "main.py")])


if __name__ == "__main__":
    mode = choose_login_mode()
    if mode in ("1", "2") and not QQ_ACCOUNT:
        print("错误：请在 .env 中填写 QQ_ACCOUNT。")
        input("按回车退出...")
        sys.exit(1)
    if mode == "2" and not QQ_PASSWORD:
        print("错误：请在 .env 中填写 QQ_PASSWORD。")
        input("按回车退出...")
        sys.exit(1)
    start_napcat(mode)
    start_main()
