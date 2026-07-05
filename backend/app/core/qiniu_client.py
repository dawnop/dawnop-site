"""七牛云 Kodo 封装：上传凭证、代理上传、私有下载 URL、stat、删除。

空间为**私有**：下载/预览均通过带签名、有时效的 URL（private_download_url）。
所有函数失败时抛 RuntimeError，由上层转换为 HTTP 错误。
"""
import json
import time
from functools import lru_cache
from urllib.parse import quote

import requests
from qiniu import Auth, BucketManager, QiniuMacAuth, put_data, put_file

from app.config import settings


@lru_cache
def _auth() -> Auth:
    if not settings.qiniu_access_key or not settings.qiniu_secret_key:
        raise RuntimeError("未配置 QINIU_ACCESS_KEY / QINIU_SECRET_KEY")
    return Auth(settings.qiniu_access_key, settings.qiniu_secret_key)


@lru_cache
def _bucket_manager() -> BucketManager:
    return BucketManager(_auth())


def _expires(expires: int | None) -> int:
    return expires or settings.qiniu_token_expires


def upload_token(key: str, expires: int | None = None) -> str:
    """签发**限定到具体 key** 的上传凭证，供前端直传（不下发密钥）。"""
    return _auth().upload_token(settings.qiniu_bucket, key, _expires(expires))


@lru_cache
def upload_host() -> str:
    """解析当前空间所在区域的上传域名，供前端直传 POST。

    通过七牛 v4/query 接口（只需公开的 AccessKey + bucket）拿到区域上传域名；
    失败时回退到默认上传域名。结果缓存（区域不会变）。
    """
    try:
        resp = requests.get(
            "https://api.qiniu.com/v4/query",
            params={"ak": settings.qiniu_access_key, "bucket": settings.qiniu_bucket},
            timeout=10,
        )
        domains = resp.json()["hosts"][0]["up"]["domains"]
        if domains:
            return f"https://{domains[0]}"
    except Exception:  # noqa: BLE001  网络/结构异常都回退默认域名
        pass
    return "https://upload.qiniup.com"


def proxy_upload(key: str, data: bytes, mime: str | None = None) -> dict:
    """服务端代理上传字节流到七牛，返回七牛响应 ret。

    整块 bytes 一次性上传，适合小内容（create-file 空文件、/save 文本编辑等）。
    大文件走 proxy_upload_file（磁盘流式、自动分片续传），避免内存峰值。
    """
    token = _auth().upload_token(settings.qiniu_bucket, key, 3600)
    ret, info = put_data(
        token, key, data, mime_type=mime or "application/octet-stream"
    )
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛上传失败: {info}")
    return ret


def proxy_upload_file(key: str, filepath: str, mime: str | None = None) -> dict:
    """从**本地文件**上传到七牛（内存恒定）。

    put_file 按大小自动选择：小文件表单直传、大文件分片续传（从磁盘按块读、
    可 seek 重试），故上传任意大小都不会把整个文件读进内存。WebDAV PUT 用它：
    先把请求体流式落到临时文件，再交此函数上传。
    """
    token = _auth().upload_token(settings.qiniu_bucket, key, 3600)
    ret, info = put_file(
        token, key, filepath, mime_type=mime or "application/octet-stream"
    )
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛上传失败: {info}")
    return ret


def private_url(
    key: str,
    expires: int | None = None,
    attname: str | None = None,
    fop: str | None = None,
) -> str:
    """生成私有空间的带签名下载/预览 URL。

    attname 不为空时通过七牛 `?attname=` 强制以附件（指定文件名）下载；
    为空则浏览器内联预览。
    fop 不为空时（如 `imageView2/2/w/320/format/webp`）追加图片处理指令，
    生成缩略图——**必须先拼 fop 再签名**（fop 是被签名 URL 的一部分）。
    """
    domain = settings.qiniu_domain.rstrip("/")
    base_url = f"{domain}/{key}"
    query = []
    if fop:
        query.append(fop)
    if attname:
        query.append(f"attname={quote(attname)}")
    if query:
        base_url += "?" + "&".join(query)
    return _auth().private_download_url(base_url, expires=_expires(expires))


def stat(key: str) -> dict:
    """查询文件元信息（fsize、mimeType、hash 等）。"""
    ret, info = _bucket_manager().stat(settings.qiniu_bucket, key)
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛 stat 失败: {info}")
    return ret


def delete(key: str) -> None:
    """删除七牛上的对象。key 不存在（612）也视为已删除。"""
    ret, info = _bucket_manager().delete(settings.qiniu_bucket, key)
    if info.status_code not in (200, 612):
        raise RuntimeError(f"七牛删除失败: {info}")


def copy(src_key: str, dst_key: str) -> None:
    """在同一空间内复制对象（用于复制文件/文件夹）。"""
    bucket = settings.qiniu_bucket
    ret, info = _bucket_manager().copy(bucket, src_key, bucket, dst_key, force="true")
    if info.status_code != 200:
        raise RuntimeError(f"七牛复制失败: {info}")


# 统计 API 调用忽略代理环境变量（开发机本机代理对七牛时好时坏，服务端直连即可）
_plain_http = requests.Session()
_plain_http.trust_env = False


# 七牛统计 API 主机（api.qiniuapi.com/v6/*）。鉴权用 Qiniu 签名（QiniuMacAuth，非 QBox）。
# 注意各端点的参数约定不一致（实测）：space/count 用 `bucket=`，blob_io 用 `$bucket=` + `select=`。
_STAT_HOST = "api.qiniuapi.com"


def _stat_get(path: str):
    """GET 七牛统计 API，返回解析后的 JSON（可能是 dict 或 list）。失败抛 RuntimeError。"""
    if not settings.qiniu_access_key or not settings.qiniu_secret_key:
        raise RuntimeError("未配置 QINIU_ACCESS_KEY / QINIU_SECRET_KEY")
    url = f"http://{_STAT_HOST}{path}"
    mac = QiniuMacAuth(settings.qiniu_access_key, settings.qiniu_secret_key)
    token = mac.token_of_request(
        method="GET", host=_STAT_HOST, url=url, qheaders="",
        content_type="application/x-www-form-urlencoded",
    )
    r = _plain_http.get(
        url,
        headers={
            "Authorization": f"Qiniu {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"七牛统计失败 {path}: {r.status_code} {r.text[:120]}")
    return r.json()


def _range(days: int) -> tuple[str, str]:
    from datetime import datetime, timedelta

    now = datetime.now()
    return (now - timedelta(days=days)).strftime("%Y%m%d000000"), now.strftime("%Y%m%d%H%M%S")


def space_series(days: int = 30) -> dict:
    """空间存储量按天序列（字节）。返回 {times:[unix秒], datas:[int]}。"""
    begin, end = _range(days)
    j = _stat_get(f"/v6/space?bucket={settings.qiniu_bucket}&begin={begin}&end={end}&g=day")
    return {
        "times": [int(t) for t in (j.get("times") or [])],
        "datas": [int(v or 0) for v in (j.get("datas") or [])],
    }


def count_series(days: int = 30) -> dict:
    """空间文件数按天序列。返回 {times, datas}。"""
    begin, end = _range(days)
    j = _stat_get(f"/v6/count?bucket={settings.qiniu_bucket}&begin={begin}&end={end}&g=day")
    return {
        "times": [int(t) for t in (j.get("times") or [])],
        "datas": [int(v or 0) for v in (j.get("datas") or [])],
    }


def flow_series(days: int = 30, select: str = "flow") -> list[dict]:
    """外网流出流量（select=flow，字节）或请求次数（select=hits）按天序列。

    返回 [{time: ISO8601, value: int}]（blob_io 的响应形状与 space/count 不同，是列表）。
    """
    begin, end = _range(days)
    arr = _stat_get(
        f"/v6/blob_io?$bucket={settings.qiniu_bucket}&select={select}&begin={begin}&end={end}&g=day"
    )
    return [{"time": it.get("time"), "value": int((it.get("values") or {}).get(select) or 0)} for it in (arr or [])]


def _last_nonzero(datas: list[int]) -> int | None:
    for v in reversed(datas):
        if v:
            return v
    return None


def bucket_space() -> int:
    """空间当前存储量（字节）：取最近 7 天最后一个非空点。

    统计的是**空间实际对象**（含 register 失败等孤儿），可能大于本地元数据求和。
    未配置密钥或无数据抛 RuntimeError，由上层兜底。
    """
    datas = space_series(days=7)["datas"]
    v = _last_nonzero(datas)
    if v is None:
        raise RuntimeError("七牛空间统计无数据")
    return v


# ---------------- 融合 CDN 用量 ----------------
# 私有空间绑的自定义域名 storage.dawnop.com 是**融合 CDN 域名**，用户下载走 CDN（302 直连）。
# CDN 流量与 Kodo 源站流出（blob_io）是两个产品：源站只有回源那部分，CDN 才是真实用户侧流量。
# CDN 统计接口用 QiniuMacAuth 签名（含 body），域名列表在 api.qiniu.com，流量/带宽在 fusion.qiniuapi.com。


def _mac_request(host: str, path: str, method: str = "GET", body: dict | None = None):
    """QiniuMacAuth 签名请求（CDN 接口用；GET 域名列表 / POST 流量带宽）。失败抛 RuntimeError。"""
    if not settings.qiniu_access_key or not settings.qiniu_secret_key:
        raise RuntimeError("未配置 QINIU_ACCESS_KEY / QINIU_SECRET_KEY")
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
    from datetime import datetime, timedelta

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
# 七牛财务 API：GET api.qiniu.com/billing-api/v1/respack/list（QiniuMacAuth 签名）。
# CDN 流量按**流量包**计费（如 500GB 年包），非月度——此接口给权威的 已用/总量/剩余。


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
