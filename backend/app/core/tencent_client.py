"""腾讯云 API 封装（监控页用）：TC3-HMAC-SHA256 签名 + Lighthouse / 云监控只读查询。

本站服务器是**轻量应用服务器 Lighthouse**（非 CVM，故不在 QCE/CVM 命名空间）：
- 流量包用量走 lighthouse `DescribeInstancesTrafficPackages`（按月计费的核心指标）；
- 实例规格/到期走 lighthouse `DescribeInstances`；
- 趋势走云监控 `GetMonitorData`（命名空间 QCE/LIGHTHOUSE）。

只用只读接口。未配置密钥（tencent_secret_id/key 为空）时函数抛 RuntimeError，
由上层降级——监控页「腾讯云」块只显示本机指标。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import requests

from app.config import settings

_http = requests.Session()
_TIMEOUT = 12


def _configured() -> bool:
    return bool(settings.tencent_secret_id and settings.tencent_secret_key)


def _sign(secret_key: str, date: str, service: str) -> bytes:
    def h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    return h(h(h(("TC3" + secret_key).encode(), date), service), "tc3_request")


def _call(
    service: str, action: str, version: str, payload: dict, region: str | None = None
) -> dict:
    """调用腾讯云 v3 接口，返回 Response 体；接口级 Error 抛 RuntimeError。"""
    if not _configured():
        raise RuntimeError("未配置腾讯云密钥（TENCENT_SECRET_ID / TENCENT_SECRET_KEY）")

    host = f"{service}.tencentcloudapi.com"
    region = region or settings.tencent_region
    body = json.dumps(payload, separators=(",", ":"))
    ts = int(time.time())
    date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"

    canonical_headers = (
        f"content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    scope = f"{date}/{service}/tc3_request"
    string_to_sign = (
        f"TC3-HMAC-SHA256\n{ts}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )
    signing_key = _sign(settings.tencent_secret_key, date, service)
    signature = hmac.new(
        signing_key, string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    authorization = (
        f"TC3-HMAC-SHA256 Credential={settings.tencent_secret_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    r = _http.post(
        f"https://{host}",
        data=body.encode(),
        headers={
            "Authorization": authorization,
            "Content-Type": ct,
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Version": version,
            "X-TC-Timestamp": str(ts),
            "X-TC-Region": region,
        },
        timeout=_TIMEOUT,
    )
    resp = r.json().get("Response", {})
    err = resp.get("Error")
    if err:
        raise RuntimeError(f"{action} 失败: {err.get('Code')} {err.get('Message')}")
    return resp


# ---------------- Lighthouse ----------------


def traffic_package() -> dict:
    """本月流量包用量（字节）。Lighthouse 按月流量包计费，这是最该盯的指标。

    返回 {used, total, remaining, overflow, start, end, status}；单位字节，时间 ISO8601。
    """
    resp = _call(
        "lighthouse",
        "DescribeInstancesTrafficPackages",
        "2020-03-24",
        {"InstanceIds": [settings.lighthouse_instance_id]},
        region=settings.tencent_region,
    )
    sets = resp.get("InstanceTrafficPackageSet") or []
    if not sets:
        raise RuntimeError("未找到该实例的流量包")
    pkgs = sets[0].get("TrafficPackageSet") or []
    if not pkgs:
        raise RuntimeError("流量包列表为空")
    p = pkgs[0]
    return {
        "used": int(p.get("TrafficUsed") or 0),
        "total": int(p.get("TrafficPackageTotal") or 0),
        "remaining": int(p.get("TrafficPackageRemaining") or 0),
        "overflow": int(p.get("TrafficOverflow") or 0),
        "start": p.get("StartTime"),
        "end": p.get("EndTime"),
        "status": p.get("Status"),
    }


def instance() -> dict:
    """实例规格/状态/到期。返回 {name, cpu, memory_gb, state, ip, disk_gb, expired_at, created_at}。"""
    resp = _call(
        "lighthouse",
        "DescribeInstances",
        "2020-03-24",
        {"InstanceIds": [settings.lighthouse_instance_id]},
        region=settings.tencent_region,
    )
    arr = resp.get("InstanceSet") or []
    if not arr:
        raise RuntimeError("未找到实例")
    s = arr[0]
    disk = s.get("SystemDisk") or {}
    addrs = s.get("PublicAddresses") or []
    return {
        "name": s.get("InstanceName"),
        "cpu": s.get("CPU"),
        "memory_gb": s.get("Memory"),
        "state": s.get("InstanceState"),
        "ip": addrs[0] if addrs else None,
        "disk_gb": disk.get("DiskSize"),
        "expired_at": s.get("ExpiredTime"),
        "created_at": s.get("CreatedTime"),
    }


# ---------------- 云监控趋势 ----------------


def monitor_series(metric: str, days: int = 30, period: int = 86400) -> dict:
    """QCE/LIGHTHOUSE 指标的时序。返回 {timestamps: [...], values: [...], unit}。

    period 秒（默认按天出点）。腾讯云要求时间为带时区的 ISO8601。
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%M:%S+00:00"
    resp = _call(
        "monitor",
        "GetMonitorData",
        "2018-07-24",
        {
            "Namespace": "QCE/LIGHTHOUSE",
            "MetricName": metric,
            "Period": period,
            "StartTime": start.strftime(fmt),
            "EndTime": end.strftime(fmt),
            "Instances": [
                {
                    "Dimensions": [
                        {"Name": "InstanceId", "Value": settings.lighthouse_instance_id}
                    ]
                }
            ],
        },
        region=settings.tencent_region,
    )
    dp = (resp.get("DataPoints") or [{}])[0]
    return {
        "timestamps": [int(t) for t in (dp.get("Timestamps") or [])],
        "values": dp.get("Values") or [],
        "unit": resp.get("Unit"),
    }
