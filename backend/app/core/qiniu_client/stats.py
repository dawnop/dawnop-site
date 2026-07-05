"""七牛 Kodo 存储/流量统计（api.qiniuapi.com/v6/*，QiniuMacAuth 签名）。

注意各端点参数约定不一致（实测）：space/count 用 `bucket=`，blob_io 用 `$bucket=` + `select=`。
"""
from datetime import datetime, timedelta

from qiniu import QiniuMacAuth

from app.config import settings

from ._common import _plain_http, _require_keys

_STAT_HOST = "api.qiniuapi.com"


def _stat_get(path: str):
    """GET 七牛统计 API，返回解析后的 JSON（可能是 dict 或 list）。失败抛 RuntimeError。"""
    _require_keys()
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
