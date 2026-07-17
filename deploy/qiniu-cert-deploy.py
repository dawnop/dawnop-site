#!/usr/bin/env python3
# acme.sh 续期后调用（部署于服务器 /home/dawn/qiniu-cert-deploy.py，由 Le_ReloadCmd 触发）：
# 把 /etc/ssl/dawnop/ 的新通配符证书上传七牛，并把 DOMAINS 里的加速域名改绑到新 certID。
# 七牛 AK/SK 从后端 .env 读，不打印。幂等：每次上传生成一张新证书对象。
import json, hmac, hashlib, base64, datetime, sys
from urllib.parse import urlparse
import urllib.request, urllib.error

ENV = "/opt/dawnop/backend/.env"
CA_PATH = "/etc/ssl/dawnop/dawnop.com_bundle.crt"
KEY_PATH = "/etc/ssl/dawnop/dawnop.com.key"
# 七牛侧用通配符证书的所有加速域名：域名 -> forceHttps
# storage 强制 https（签名 URL 全是 https）；cdn 不强制（回源探测走 http 也能通）
DOMAINS = {"storage.dawnop.com": True, "cdn.dawnop.com": False}


def load_env(path):
    d = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip('"').strip("'")
    return d


env = load_env(ENV)
AK = env["QINIU_ACCESS_KEY"]
SK = env["QINIU_SECRET_KEY"].encode()


def b64url(b):
    return base64.urlsafe_b64encode(b).decode()


def qbox(url):
    p = urlparse(url)
    s = p.path + ("?" + p.query if p.query else "") + "\n"
    return "QBox %s:%s" % (AK, b64url(hmac.new(SK, s.encode(), hashlib.sha1).digest()))


def req(method, url, obj):
    body = json.dumps(obj)
    r = urllib.request.Request(url, data=body.encode(), method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", qbox(url))
    try:
        resp = urllib.request.urlopen(r)
        return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


ca = open(CA_PATH).read()
pri = open(KEY_PATH).read()
name = "dawnop-wildcard-" + datetime.date.today().strftime("%Y%m%d")

st, rp = req(
    "POST",
    "https://api.qiniu.com/sslcert",
    {"name": name, "common_name": "*.dawnop.com", "pri": pri, "ca": ca},
)
print("[sslcert]", st, rp)
if st != 200:
    sys.exit("cert upload failed")
cert_id = json.loads(rp)["certID"]

fail = False
for domain, force in DOMAINS.items():
    st2, rp2 = req(
        "PUT",
        "https://api.qiniu.com/domain/%s/httpsconf" % domain,
        {"certId": cert_id, "forceHttps": force, "http2Enable": True},
    )
    print("[httpsconf]", domain, st2, rp2)
    if st2 != 200:
        fail = True
if fail:
    sys.exit("httpsconf update failed")
print("OK bound %s -> certId=%s" % (", ".join(DOMAINS), cert_id))
