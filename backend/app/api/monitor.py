"""监控页后端：聚合三方资源用量，供后台「监控」页展示。

三块，各自独立容错（一块取数失败不影响其余，返回该块 {"error": ...}）：
- server：本机 CPU/内存/磁盘/网络（psutil，后端与被监控机同机，实时、免密钥）。
- lighthouse：腾讯云轻量服务器流量包/实例/CPU 趋势（tencent_client，需只读子账号密钥）。
- qiniu：七牛空间存储/文件数/外网流出流量/请求次数 + 30 天趋势。
- vault：Vaultwarden 存活 + 版本 + 时延（内网 HTTP 探测）。

三方结果（lighthouse/qiniu）带 TTL 缓存，避免频繁打第三方 API；server/vault 每次实时。
`?refresh=1` 跳过缓存强制刷新。
"""
from __future__ import annotations

import os
import time

import psutil
import requests
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.settings import get_setting
from app.config import settings
from app.core import qiniu_client, tencent_client
from app.deps import get_current_user, get_db

router = APIRouter()

_TTL = 120  # 三方数据缓存秒数
_cache: dict = {"at": 0.0, "lighthouse": None, "qiniu": None}

_vault_http = requests.Session()
_vault_http.trust_env = False


# ---------------- 本机（psutil）----------------


def _server() -> dict:
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:  # 某些平台（Windows）无 loadavg
        load1 = load5 = load15 = None
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "cpu_count": psutil.cpu_count(),
        "load": [load1, load5, load15],
        "mem": {"total": vm.total, "used": vm.total - vm.available, "percent": vm.percent},
        "disk": {"total": du.total, "used": du.used, "percent": du.percent},
        "net": {"sent": net.bytes_sent, "recv": net.bytes_recv},  # 自开机累计
        "boot_time": int(psutil.boot_time()),
        "uptime": int(time.time() - psutil.boot_time()),
    }


# ---------------- 腾讯云 Lighthouse ----------------


def _lighthouse() -> dict:
    if not (settings.tencent_secret_id and settings.tencent_secret_key):
        return {"configured": False}
    out: dict = {"configured": True}
    try:
        out["traffic"] = tencent_client.traffic_package()
    except Exception as e:  # noqa: BLE001 每块独立容错
        out["traffic_error"] = str(e)
    try:
        out["instance"] = tencent_client.instance()
    except Exception as e:  # noqa: BLE001
        out["instance_error"] = str(e)
    try:
        s = tencent_client.monitor_series("CpuUsage", days=30)
        out["cpu_trend"] = [{"t": t, "v": v} for t, v in zip(s["timestamps"], s["values"])]
    except Exception as e:  # noqa: BLE001
        out["cpu_trend_error"] = str(e)
    return out


# ---------------- 七牛 Kodo ----------------


def _qiniu() -> dict:
    if not (settings.qiniu_access_key and settings.qiniu_secret_key):
        return {"configured": False}
    out: dict = {"configured": True, "bucket": settings.qiniu_bucket}
    try:
        space = qiniu_client.space_series(days=30)
        count = qiniu_client.count_series(days=30)
        flow = qiniu_client.flow_series(days=30, select="flow")
        hits = qiniu_client.flow_series(days=30, select="hits")
        out["space"] = qiniu_client._last_nonzero(space["datas"]) or 0
        out["count"] = qiniu_client._last_nonzero(count["datas"]) or 0
        out["flow_30d"] = sum(x["value"] for x in flow)
        out["hits_30d"] = sum(x["value"] for x in hits)
        out["space_trend"] = [{"t": t, "v": v} for t, v in zip(space["times"], space["datas"])]
        out["flow_trend"] = [{"t": x["time"], "v": x["value"]} for x in flow]
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    # CDN 流量（融合 CDN，真实用户侧下载量）独立容错：拿不到不影响源站/存储展示。
    # 配额条用**流量包**（respack，权威的已用/总量/剩余，按包非按月）；趋势/峰值用近 30 天日志。
    try:
        cdn = qiniu_client.cdn_flux_series(days=30)
        out["cdn_flow_30d"] = sum(cdn["values"])
        out["cdn_peak_bps"] = qiniu_client.cdn_bandwidth_peak(days=30)
        out["cdn_domains"] = cdn["domains"]
        out["cdn_trend"] = [{"t": t, "v": v} for t, v in zip(cdn["times"], cdn["values"])]
    except Exception as e:  # noqa: BLE001
        out["cdn_error"] = str(e)
    try:
        rp = qiniu_client.respack_summary()
        out["cdn_pack"] = rp["cdn"]
        out["storage_pack"] = rp["storage"]
    except Exception as e:  # noqa: BLE001
        out["respack_error"] = str(e)
    return out


# ---------------- Vaultwarden ----------------


def _vault() -> dict:
    base = settings.vault_alive_url.rsplit("/alive", 1)[0]
    out: dict = {"url": base}
    t0 = time.time()
    try:
        r = _vault_http.get(settings.vault_alive_url, timeout=5)
        out["alive"] = r.status_code == 200
        out["latency_ms"] = round((time.time() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        out["alive"] = False
        out["error"] = str(e)
        return out
    try:
        cfg = _vault_http.get(f"{base}/api/config", timeout=5).json()
        out["version"] = cfg.get("version")
    except Exception:  # noqa: BLE001 版本拿不到不影响存活判断
        pass
    return out


@router.get("", summary="监控总览：本机 / 腾讯云 / 七牛 / Vault 用量快照 + 趋势")
def overview(
    refresh: bool = Query(False, description="跳过三方数据缓存强制刷新"),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    now = time.time()
    if refresh or _cache["lighthouse"] is None or now - _cache["at"] > _TTL:
        _cache["lighthouse"] = _lighthouse()
        _cache["qiniu"] = _qiniu()
        _cache["at"] = now
    # 存储配额从全局设置（DB）实时读取覆盖——存储用量条分母，改设置即时生效（不受缓存影响）。
    # CDN 用流量包（respack）自身的总量，无需配额设置。
    qn = _cache["qiniu"]
    if qn and qn.get("configured"):
        qn["quota"] = int(get_setting(db, "storage_quota_gb")) * 1024**3
    return {
        "server": _server(),
        "lighthouse": _cache["lighthouse"],
        "qiniu": qn,
        "vault": _vault(),
        "cached_at": int(_cache["at"]),
    }
