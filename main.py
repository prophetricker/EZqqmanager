#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地 QQ 群消息自动定时发送守护脚本

功能概述：
1. 使用飞书 internal 鉴权接口获取 tenant_access_token，并做过期缓存。
2. 每分钟轮询一次飞书多维表格，筛选“执行状态=待发送”的记录。
3. 当“当前时间 >= 计划发送时间”时，调用本地 NapCatQQ 接口发送群消息。
4. 根据 NapCatQQ 的 HTTP 响应码，回写飞书记录状态为“已发送/发送失败”。
"""

from __future__ import annotations

import logging
import os
import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import schedule
from dotenv import load_dotenv


REQUEST_TIMEOUT = 30
STATUS_PENDING = "待发送"
STATUS_SENT = "已发送"
STATUS_FAILED = "发送失败"


@dataclass
class Settings:
    """环境变量配置。"""

    feishu_app_id: str
    feishu_app_secret: str
    feishu_bitable_app_token: str
    feishu_table_id: str
    feishu_api_base: str
    napcat_api_base: str
    napcat_api_path: str
    napcat_access_token: str
    poll_interval_minutes: int
    field_group_id: str
    field_content: str
    field_image: str
    field_plan_time: str
    field_status: str

    @classmethod
    def from_env(cls) -> "Settings":
        missing = []
        required_keys = [
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "FEISHU_BITABLE_APP_TOKEN",
            "FEISHU_TABLE_ID",
        ]
        for key in required_keys:
            if not os.getenv(key):
                missing.append(key)
        if missing:
            raise ValueError(f"缺少必要环境变量: {', '.join(missing)}")

        poll_minutes = max(1, int(os.getenv("POLL_INTERVAL_MINUTES", "1")))
        napcat_api_path = os.getenv("NAPCAT_API_PATH", "/send_group_msg").strip()
        if not napcat_api_path:
            napcat_api_path = "/send_group_msg"
        if not napcat_api_path.startswith("/"):
            napcat_api_path = "/" + napcat_api_path

        return cls(
            feishu_app_id=os.environ["FEISHU_APP_ID"],
            feishu_app_secret=os.environ["FEISHU_APP_SECRET"],
            feishu_bitable_app_token=os.environ["FEISHU_BITABLE_APP_TOKEN"],
            feishu_table_id=os.environ["FEISHU_TABLE_ID"],
            feishu_api_base=os.getenv("FEISHU_API_BASE", "https://open.feishu.cn").rstrip("/"),
            napcat_api_base=os.getenv("NAPCAT_API_BASE", "http://127.0.0.1:3000").rstrip("/"),
            napcat_api_path=napcat_api_path,
            napcat_access_token=os.getenv("NAPCAT_ACCESS_TOKEN", "").strip(),
            poll_interval_minutes=poll_minutes,
            field_group_id=os.getenv("FIELD_GROUP_ID", "群号"),
            field_content=os.getenv("FIELD_CONTENT", "公告内容"),
            field_image=os.getenv("FIELD_IMAGE", "公告图片链接"),
            field_plan_time=os.getenv("FIELD_PLAN_TIME", "计划发送时间"),
            field_status=os.getenv("FIELD_STATUS", "执行状态"),
        )


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def normalize_text(value: Any) -> str:
    """
    将飞书字段值尽可能归一化成字符串。
    兼容字符串、数字、列表、字典等常见结构，避免字段类型差异导致脚本崩溃。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "name", "url", "link", "value"):
            if key in value:
                return normalize_text(value[key])
        return str(value).strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            text = normalize_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return str(value).strip()


def parse_plan_time(value: Any) -> datetime | None:
    """
    解析飞书“计划发送时间”字段为本地时区时间。
    优先兼容毫秒级/秒级时间戳，也兼容 ISO 时间字符串。
    """
    if value is None:
        return None

    if isinstance(value, dict):
        for key in ("timestamp", "time", "value"):
            if key in value:
                return parse_plan_time(value[key])
        return None

    if isinstance(value, list):
        if not value:
            return None
        return parse_plan_time(value[0])

    if isinstance(value, (int, float)):
        ts = float(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.replace(".", "", 1).isdigit():
            ts = float(raw)
        else:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                logging.warning("无法解析计划发送时间字符串: %s", raw)
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt.astimezone()
    else:
        logging.warning("不支持的计划发送时间类型: %s", type(value))
        return None

    # 飞书常见为毫秒时间戳（13位），这里自动降维到秒。
    if ts > 1e12:
        ts = ts / 1000.0
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt_utc.astimezone()


class FeishuAuth:
    """飞书 tenant_access_token 管理（含过期缓存）。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token: str | None = None
        self._expire_at = 0.0

    def get_token(self, force_refresh: bool = False) -> str:
        now = time.time()
        if not force_refresh and self._token and now < self._expire_at:
            return self._token
        self._refresh_token()
        if not self._token:
            raise RuntimeError("飞书 tenant_access_token 为空")
        return self._token

    def _refresh_token(self) -> None:
        url = f"{self.settings.feishu_api_base}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.settings.feishu_app_id,
            "app_secret": self.settings.feishu_app_secret,
        }
        logging.info("正在刷新飞书 tenant_access_token")
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            logging.info("飞书鉴权接口响应 HTTP %s", resp.status_code)
            resp.raise_for_status()
            body = resp.json()
        except requests.exceptions.RequestException:
            logging.exception("飞书鉴权请求失败")
            raise
        except ValueError:
            logging.exception("飞书鉴权响应不是合法 JSON")
            raise

        if body.get("code") != 0:
            raise RuntimeError(f"飞书鉴权失败: code={body.get('code')} msg={body.get('msg')}")

        token = body.get("tenant_access_token")
        expire_seconds = int(body.get("expire", 7200))
        if not token:
            raise RuntimeError("飞书鉴权成功但 tenant_access_token 缺失")

        # 预留 60 秒缓冲，避免临界点过期。
        self._token = token
        self._expire_at = time.time() + max(expire_seconds - 60, 60)
        logging.info("tenant_access_token 已更新，有效期约 %s 秒", expire_seconds)


class FeishuBitableClient:
    """飞书多维表格 API 客户端。"""

    def __init__(self, settings: Settings, auth: FeishuAuth) -> None:
        self.settings = settings
        self.auth = auth
        self._last_req_monotonic = 0.0
        # 飞书限制 10 QPS，这里主动降到 ~8.3 QPS 作为保护缓冲。
        self._min_interval_sec = 0.12

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retried: bool = False,
    ) -> dict[str, Any]:
        token = self.auth.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        url = f"{self.settings.feishu_api_base}{path}"

        now_m = time.monotonic()
        elapsed = now_m - self._last_req_monotonic
        if elapsed < self._min_interval_sec:
            time.sleep(self._min_interval_sec - elapsed)

        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=REQUEST_TIMEOUT,
            )
            self._last_req_monotonic = time.monotonic()
            logging.info("飞书 API %s %s -> HTTP %s", method.upper(), path, resp.status_code)
        except requests.exceptions.RequestException:
            logging.exception("飞书 API 请求异常: %s %s", method.upper(), path)
            raise

        if resp.status_code == 401 and not retried:
            logging.warning("飞书 API 返回 401，尝试强制刷新 token 后重试一次")
            self.auth.get_token(force_refresh=True)
            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                retried=True,
            )

        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError(f"飞书 API 响应非 JSON: {resp.text[:200]}")

        if resp.status_code >= 400:
            raise RuntimeError(f"飞书 API HTTP 错误: {resp.status_code}, body={body}")
        if body.get("code") != 0:
            raise RuntimeError(f"飞书 API 业务错误: code={body.get('code')} msg={body.get('msg')}")

        return body.get("data", {})

    def list_pending_records(self) -> list[dict[str, Any]]:
        """
        拉取“执行状态=待发送”的记录。
        使用 records 列表接口 + filter 公式，避免拉全表。
        """
        path = (
            f"/open-apis/bitable/v1/apps/{self.settings.feishu_bitable_app_token}"
            f"/tables/{self.settings.feishu_table_id}/records"
        )
        filter_formula = f'CurrentValue.[{self.settings.field_status}] = "{STATUS_PENDING}"'

        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {
                "page_size": 500,
                "filter": filter_formula,
            }
            if page_token:
                params["page_token"] = page_token

            data = self._request("GET", path, params=params)
            items = data.get("items", [])
            records.extend(items)

            has_more = bool(data.get("has_more"))
            page_token = data.get("page_token", "")
            if not has_more:
                break
            if not page_token:
                logging.warning("飞书返回 has_more=true 但 page_token 为空，提前结束分页")
                break

        return records

    def update_record_status(self, record_id: str, status_text: str) -> None:
        """更新单条记录的执行状态。"""
        path = (
            f"/open-apis/bitable/v1/apps/{self.settings.feishu_bitable_app_token}"
            f"/tables/{self.settings.feishu_table_id}/records/{record_id}"
        )
        body = {"fields": {self.settings.field_status: status_text}}
        self._request("PUT", path, json_body=body)


def build_group_message_payload(content: str, image_text: str) -> str | list[dict[str, Any]]:
    """
    构造 OneBot 群消息内容：
    - 纯文本：直接用字符串
    - 文本 + 图片：使用消息段数组（text + image）
    """
    if not image_text:
        return content
    return [
        {"type": "text", "data": {"text": content}},
        {"type": "image", "data": {"file": image_text}},
    ]


def send_group_message_via_napcat(settings: Settings, payload: dict[str, Any]) -> tuple[bool, int | None, str]:
    """调用本地 NapCatQQ 发送群消息并根据返回体做业务级成功判定。"""
    url = f"{settings.napcat_api_base}{settings.napcat_api_path}"
    headers: dict[str, str] = {}
    if settings.napcat_access_token:
        headers["Authorization"] = f"Bearer {settings.napcat_access_token}"

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        body_text = (resp.text or "").strip()
        short_body = body_text[:300]
        logging.info("NapCat 接口响应 HTTP %s，body=%s", resp.status_code, short_body)

        # 先按 HTTP 层判断；若非 2xx，直接失败。
        if not (200 <= resp.status_code < 300):
            return False, resp.status_code, short_body

        # 2xx 还不够，继续按业务层判断（避免“HTTP 200 但业务失败”被误判）。
        ok, detail = parse_napcat_business_success(body_text, require_message_id=False)
        return ok, resp.status_code, detail
    except requests.exceptions.RequestException as exc:
        logging.exception("NapCat 请求异常")
        return False, None, str(exc)


def parse_napcat_business_success(body_text: str, require_message_id: bool = False) -> tuple[bool, str]:
    """
    解析 NapCat 返回体，判断业务是否成功。
    兼容：
    1) 标准 JSON（status/retcode/code/errCode）
    2) JWT 字符串（某些服务会把业务错误放在 JWT payload）
    """
    if not body_text:
        return True, "empty-body"

    # 分支 1：标准 JSON
    try:
        data = json.loads(body_text)
        if isinstance(data, dict):
            status = str(data.get("status", "")).lower()
            retcode = data.get("retcode", data.get("code", data.get("errCode", 0)))
            msg = (
                str(data.get("message", ""))
                or str(data.get("msg", ""))
                or str(data.get("errMsg", ""))
            )
            message_id = None
            if isinstance(data.get("data"), dict):
                message_id = data["data"].get("message_id")

            # OneBot 常见成功形态：status=ok 且 retcode=0
            if status in ("ok", "success") and str(retcode) in ("0", "0.0"):
                if require_message_id and message_id is None:
                    return False, "business-ambiguous success-without-message_id"
                return True, f"status={status}, retcode={retcode}, message_id={message_id}"
            # 兼容仅返回 code=0 的场景
            if status == "" and str(retcode) in ("0", "0.0"):
                if require_message_id and message_id is None:
                    return False, "business-ambiguous success-without-message_id"
                return True, f"retcode={retcode}, message_id={message_id}"
            # NapCat retcode=200 是 QQ 内核 NTEvent 超时，消息实际已发出
            if str(retcode) == "200":
                return True, f"nt-timeout-but-sent retcode={retcode}"
            return False, f"business-failed retcode={retcode}, msg={msg}"
    except ValueError:
        pass

    # 分支 2：JWT 文本，尝试读取 payload 中的 errCode/errMsg
    jwt_info = decode_jwt_payload(body_text)
    if jwt_info is not None:
        err_code = jwt_info.get("errCode", 0)
        err_msg = jwt_info.get("errMsg", "")
        if str(err_code) in ("0", "0.0"):
            return True, f"jwt errCode={err_code}"
        return False, f"jwt business-failed errCode={err_code}, errMsg={err_msg}"

    # 既不是 JSON 也不是 JWT：保守判定失败，防止误报成功。
    return False, f"unrecognized-body: {body_text[:200]}"


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """尝试解码 JWT payload（不验签，只用于读取业务错误信息）。"""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        import base64

        pad = "=" * ((4 - len(payload) % 4) % 4)
        raw = base64.urlsafe_b64decode(payload + pad)
        obj = json.loads(raw.decode("utf-8", errors="ignore"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


class NoticeDaemon:
    """轮询并执行群消息任务的守护器。"""

    def __init__(self, settings: Settings, bitable_client: FeishuBitableClient) -> None:
        self.settings = settings
        self.bitable_client = bitable_client

    def poll_once(self) -> None:
        started = time.time()
        logging.info("========== 开始轮询任务 ==========")
        try:
            records = self.bitable_client.list_pending_records()
        except Exception:
            logging.exception("拉取待发送记录失败，本轮结束")
            return

        logging.info("本轮拉取到待发送记录 %d 条", len(records))
        now = datetime.now().astimezone()
        for record in records:
            self._process_record(record, now)

        elapsed = time.time() - started
        logging.info("========== 本轮结束，耗时 %.2f 秒 ==========", elapsed)

    def _process_record(self, record: dict[str, Any], now: datetime) -> None:
        record_id = str(record.get("record_id", ""))
        fields = record.get("fields") or {}
        if not record_id:
            logging.warning("发现缺失 record_id 的记录，已跳过")
            return

        plan_time = parse_plan_time(fields.get(self.settings.field_plan_time))
        if plan_time is None:
            logging.warning("[%s] 计划发送时间为空或无法解析，回写发送失败", record_id)
            self._safe_update_status(record_id, STATUS_FAILED)
            return

        if now < plan_time:
            logging.info(
                "[%s] 未到发送时间，当前=%s 计划=%s",
                record_id,
                now.strftime("%Y-%m-%d %H:%M:%S"),
                plan_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            return

        group_raw = fields.get(self.settings.field_group_id)
        content_raw = fields.get(self.settings.field_content)
        image_raw = fields.get(self.settings.field_image)

        try:
            group_text = normalize_text(group_raw).splitlines()[0].strip()
            group_id = int(group_text)
        except Exception:
            logging.exception("[%s] 群号字段无效，回写发送失败。原始值=%s", record_id, group_raw)
            self._safe_update_status(record_id, STATUS_FAILED)
            return

        content = normalize_text(content_raw)
        if not content:
            logging.warning("[%s] 消息内容为空，回写发送失败", record_id)
            self._safe_update_status(record_id, STATUS_FAILED)
            return

        image_text = normalize_text(image_raw)
        payload: dict[str, Any] = {
            "group_id": group_id,
            "message": build_group_message_payload(content, image_text),
        }

        logging.info(
            "[%s] 准备发送群消息，group_id=%s，has_image=%s",
            record_id,
            group_id,
            bool(image_text),
        )
        success, status_code, resp_text = send_group_message_via_napcat(self.settings, payload)

        if success:
            logging.info("[%s] 发送成功，NapCat HTTP=%s", record_id, status_code)
            self._safe_update_status(record_id, STATUS_SENT)
        else:
            logging.error(
                "[%s] 发送失败，NapCat HTTP=%s，响应=%s",
                record_id,
                status_code,
                resp_text,
            )
            self._safe_update_status(record_id, STATUS_FAILED)

    def _safe_update_status(self, record_id: str, status_text: str) -> None:
        try:
            self.bitable_client.update_record_status(record_id, status_text)
            logging.info("[%s] 已回写执行状态 -> %s", record_id, status_text)
        except Exception:
            logging.exception("[%s] 回写执行状态失败，目标状态=%s", record_id, status_text)


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    dotenv_path = script_dir / ".env"

    # 固定优先读取脚本同目录 .env，避免启动目录不同导致读不到配置。
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path)
    else:
        # 兜底：仍允许从当前目录或系统环境变量读取配置。
        load_dotenv()

    setup_logging()
    if dotenv_path.exists():
        logging.info("已加载配置文件: %s", dotenv_path)
    else:
        logging.warning(
            "未找到配置文件: %s，将尝试当前目录/系统环境变量。建议先创建该文件。",
            dotenv_path,
        )

    try:
        settings = Settings.from_env()
    except Exception:
        logging.exception("配置加载失败，请检查 .env")
        logging.error(
            "请在 %s 中填写必填项：FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_BITABLE_APP_TOKEN、FEISHU_TABLE_ID",
            dotenv_path,
        )
        raise

    auth = FeishuAuth(settings)
    bitable_client = FeishuBitableClient(settings, auth)
    daemon = NoticeDaemon(settings, bitable_client)

    # 启动后先跑一轮，避免刚启动时必须等待整整 1 分钟。
    daemon.poll_once()

    schedule.every(settings.poll_interval_minutes).minutes.do(daemon.poll_once)
    logging.info("守护脚本已启动，轮询间隔=%d 分钟", settings.poll_interval_minutes)

    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logging.info("收到终止信号，脚本退出")
            break
        except Exception:
            # 主循环兜底，确保异常后不会进入高频死循环，至少等待 60 秒再继续。
            logging.exception("主循环出现异常，60 秒后继续")
            time.sleep(60)


if __name__ == "__main__":
    main()
