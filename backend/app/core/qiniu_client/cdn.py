"""融合 CDN 用量 + 资源包（流量包/存储包）余额。

私有空间绑的自定义域名（storage.dawnop.com）是**融合 CDN 域名**，用户下载走 CDN（302 直连）。
CDN 流量与 Kodo 源站流出（blob_io）是两个产品：源站只有回源那部分，CDN 才是真实用户侧流量。
CDN/财务接口用 QiniuMacAuth 签名（含 body）：域名列表在 api.qiniu.com，流量/带宽在 fusion.qiniuapi.com，
资源包在 api.qiniu.com/billing-api（无公开文档，实测可用）。
"""
import json
import time
from datetime import datetime, timedelta

from qiniu import QiniuMacAuth

from app.config import settings

from ._common import _plain_http, _require_keys


def _mac_request(host: str, path: str, method: str = "GET", body: dict | None = None):
    """QiniuMacAuth 签名请求（CDN/财务接口用；GET 域名列表 / POST 流量带宽）。失败抛 RuntimeError。"""
    _require_keys()
    url = f"http://{host}{path}"
    data = json.dumps(body) if body is not None else None
    mac = QiniuMacAuth(settings.qiniu_access_key, settings.qiniu_secret_key)
    token = mac.token_of_request(
        method=method, host=host, url=url, qheaders="",
        content_type="application/json", body=data,
    )
    r = _plain_http.request(
        method, url,
        data=data.encode() if data else None,
        headers={"Authorization": f"Qiniu {token}", "Content-Type": "application/json"},
        timeout=12,
    )
    if r.status_code != 200:
        raise RuntimeError(f"七牛 CDN 接口失败 {path}: {r.status_code} {r.text[:120]}")
    return r.json()


def cdn_domains() -> list[str]:
    """账号下所有 CDN 加速域名（含测试域名）。"""
    j = _mac_request("api.qiniu.com", "/domain?limit=100")
    return [d["name"] for d in (j.get("domains") or []) if d.get("name")]


def _cdn_tune(kind: str, days: int, domains: list[str]) -> dict:
    """flux(流量,字节) 或 bandwidth(带宽,bps) 的按天序列（跨所有域名/大区汇总）。

    返回 {times:[unix秒], values:[int]}。CDN 接口按北京时间日期分点。
    """
    if not domains:
        return {"times": [], "values": []}
    end = datetime.now()
    start = end - timedelta(days=days)
    j = _mac_request(
        "fusion.qiniuapi.com",
        f"/v2/tune/{kind}",
        "POST",
        {
            "domains": ";".join(domains),
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
            "granularity": "day",
        },
    )
    time_strs = j.get("time") or []
    totals = [0] * len(time_strs)
    for _dom, regions in (j.get("data") or {}).items():
        for _region, arr in (regions or {}).items():
            for i, v in enumerate(arr or []):
                if v and i < len(totals):
                    totals[i] += int(v)
    times = [int(datetime.strptime(t, "%Y-%m-%d %H:%M:%S").timestamp()) for t in time_strs]
    return {"times": times, "values": totals}


def cdn_flux_series(days: int = 30) -> dict:
    """CDN 外网流出流量（字节）按天序列。返回 {times, values, domains}。"""
    domains = cdn_domains()
    s = _cdn_tune("flux", days, domains)
    s["domains"] = domains
    return s


def cdn_bandwidth_peak(days: int = 30) -> int:
    """CDN 峰值带宽（bps），取序列最大值。"""
    s = _cdn_tune("bandwidth", days, cdn_domains())
    return max(s["values"]) if s["values"] else 0


# ---------------- 资源包（流量包/存储包）余额 ----------------

_GB = 1024**3
# respack 财务接口有速率限制且变化慢；fm/stats 会高频调用（每次列目录），故带 TTL 缓存
_respack_cache: dict = {"at": 0.0, "val": None}


def respack_summary(force: bool = False) -> dict:
    """CDN 流量包 + 存储资源包 概览（TTL 缓存 300s）。返回 {cdn:{...}|None, storage:{...}|None}。"""
    now = time.time()
    if not force and _respack_cache["val"] is not None and now - _respack_cache["at"] < 300:
        return _respack_cache["val"]
    val = _respack_fetch()
    _respack_cache["val"] = val
    _respack_cache["at"] = now
    return val


def _respack_fetch() -> dict:
    """实际拉取资源包概览（无缓存）。

    - CDN（流量）：用 list 的累计 used_amount/total_amount（按流量计，权威）→ {used,total,remain,expire,names}。
    - 存储（容量）：用 month-overview 的 total_surplus 作**容量**（list 的 total_amount 是 GB·月不适合当容量），
      有效期取 list → {capacity,expire,names}。存储条的分子用实际占用空间（另由 space 提供），非此处 used。
    识别：status==2（生效中）；名字含「流量」= CDN、含「存储」= 存储。单位 GB→字节。
    """
    out: dict = {"cdn": None, "storage": None}
    active = [d for d in (_mac_request("api.qiniu.com", "/billing-api/v1/respack/list").get("data") or [])
              if d.get("status") == 2]

    cdn_items = [d for d in active if "流量" in (d.get("respack_name") or "")]
    if cdn_items:
        used = sum(float(d.get("used_amount") or 0) for d in cdn_items) * _GB
        total = sum(float(d.get("total_amount") or 0) for d in cdn_items) * _GB
        ends = [d["effective_end"] for d in cdn_items if d.get("effective_end")]
        out["cdn"] = {
            "used": int(used), "total": int(total), "remain": int(max(0, total - used)),
            "expire": min(ends) if ends else None,
            "names": [d.get("respack_name") for d in cdn_items],
        }

    store_items = [d for d in active if "存储" in (d.get("respack_name") or "")]
    overview = _mac_request("api.qiniu.com", "/billing-api/v1/respack/month-overview").get("data") or []
    store_ov = [o for o in overview if "存储" in (o.get("item_name") or "")]
    if store_ov:
        capacity = sum(float(o.get("total_surplus") or 0) for o in store_ov) * _GB
        ends = [d["effective_end"] for d in store_items if d.get("effective_end")]
        out["storage"] = {
            "capacity": int(capacity), "expire": min(ends) if ends else None,
            "names": [o.get("item_name") for o in store_ov],
        }
    return out
