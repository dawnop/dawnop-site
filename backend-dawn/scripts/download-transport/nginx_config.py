"""Render an isolated stock nginx instance; never read the system configuration."""

import re
from urllib.parse import urlsplit


def quoted(path):
    value = str(path)
    if any(c in value for c in '\r\n"\\$'):
        raise ValueError("unsafe configuration path")
    return '"' + value + '"'


def render(root, port, application_port, origin, connect, ca, *, idle="1s"):
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not re.fullmatch(r"[a-zA-Z0-9.-]+(?::[0-9]+)?", parsed.netloc)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"127\.0\.0\.1:[0-9]+", connect)
        or not re.fullmatch(r"[0-9]+(?:ms|s)", idle)
    ):
        raise ValueError("invalid isolated download configuration")
    # error_page accepts only 300..599. 612 is a measured release blocker.
    errors = " ".join(
        str(n) for n in range(300, 600) if n not in {304, 404, 412, 416, 499, 503, 504}
    )
    headers = "\n".join(
        f"proxy_set_header {name} $http_{name.lower().replace('-', '_')};"
        for name in (
            "Range",
            "If-Range",
            "If-Match",
            "If-None-Match",
            "If-Modified-Since",
            "If-Unmodified-Since",
        )
    )
    return f"""daemon off;
master_process on;
worker_processes 1;
pid {quoted(root / "nginx.pid")};
error_log /dev/null crit;
events {{ worker_connections 128; }}
http {{
  access_log off;
  map $status $download_response_type {{
    ~^2[0-9][0-9]$ $download_type;
    304 $download_type;
    default $upstream_http_content_type;
  }}
  client_body_temp_path {quoted(root / "client-temp")};
  proxy_temp_path {quoted(root / "proxy-temp")};
  fastcgi_temp_path {quoted(root / "fastcgi-temp")};
  uwsgi_temp_path {quoted(root / "uwsgi-temp")};
  scgi_temp_path {quoted(root / "scgi-temp")};
  limit_conn_zone $server_name zone=downloads:1m;
  upstream download_object {{ server {connect}; }}
  server {{
    listen 127.0.0.1:{port};
    server_name isolated.invalid;
    gzip off;
    send_timeout {idle};
    location = /api/fm/content {{
      proxy_pass http://127.0.0.1:{application_port};
      proxy_http_version 1.1;
      proxy_set_header Connection close;
      proxy_set_header X-Dawn-Download-Target "";
      proxy_set_header X-Dawn-Download-Type "";
      proxy_set_header X-Accel-Redirect "";
      proxy_buffering off;
      proxy_next_upstream off;
    }}
    location = /_dawn_object {{
      internal;
      if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
      # Freeze FIRST upstream fields in rewrite phase, before the object proxy.
      set $download_target $upstream_http_x_dawn_download_target;
      set $download_type $upstream_http_x_dawn_download_type;
      set $download_origin $upstream_http_access_control_allow_origin;
      set $download_credentials $upstream_http_access_control_allow_credentials;
      set $download_vary $upstream_http_vary;
      if ($download_target = "") {{ return 502; }}
      limit_conn downloads 4;
      limit_conn_status 503;
      proxy_pass https://download_object$download_target;
      proxy_http_version 1.1;
      proxy_set_header Connection close;
      proxy_pass_request_headers off;
      proxy_pass_request_body off;
      proxy_set_header Content-Length "";
      proxy_set_header Host {parsed.netloc};
      proxy_set_header Accept-Encoding identity;
      {headers}
      proxy_ssl_verify on;
      proxy_ssl_trusted_certificate {quoted(ca)};
      proxy_ssl_server_name on;
      proxy_ssl_name {parsed.hostname};
      proxy_ssl_session_reuse off;
      proxy_connect_timeout {idle};
      proxy_read_timeout {idle};
      proxy_send_timeout {idle};
      proxy_ignore_client_abort off;
      proxy_next_upstream off;
      proxy_buffering off;
      proxy_cache off;
      proxy_store off;
      proxy_max_temp_file_size 0;
      proxy_ignore_headers X-Accel-Redirect X-Accel-Buffering X-Accel-Limit-Rate X-Accel-Expires X-Accel-Charset;
      proxy_hide_header X-Accel-Redirect;
      proxy_hide_header X-Accel-Buffering;
      proxy_hide_header X-Accel-Limit-Rate;
      proxy_hide_header X-Accel-Expires;
      proxy_hide_header X-Accel-Charset;
      proxy_hide_header X-Dawn-Download-Target;
      proxy_hide_header X-Dawn-Download-Type;
      proxy_hide_header Location;
      proxy_hide_header WWW-Authenticate;
      proxy_hide_header Set-Cookie;
      proxy_hide_header Content-Type;
      proxy_hide_header Access-Control-Allow-Origin;
      proxy_hide_header Access-Control-Allow-Credentials;
      proxy_hide_header Vary;
      proxy_redirect off;
      proxy_intercept_errors on;
      error_page {errors} =502 @download_failure;
      error_page 404 = @download_not_found;
      error_page 503 = @download_unavailable;
      error_page 504 =504 @download_timeout;
      types {{ }}
      default_type "";
      add_header Content-Type $download_response_type always;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Access-Control-Allow-Credentials $download_credentials always;
      add_header Vary $download_vary always;
      add_header X-Content-Type-Options nosniff always;
    }}
    location @download_not_found {{
      # internal's own 404 has no upstream. Do not remap that access refusal.
      if ($upstream_status = "") {{ return 404; }}
      default_type application/json;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Vary $download_vary always;
      return 502 '{{"detail":"object download failed"}}';
    }}
    location @download_unavailable {{
      # A connection-limit refusal follows only the application's 200 handoff.
      if ($upstream_status = "200") {{ return 503; }}
      default_type application/json;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Vary $download_vary always;
      return 502 '{{"detail":"object download failed"}}';
    }}
    location @download_failure {{
      default_type application/json;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Vary $download_vary always;
      return 502 '{{"detail":"object download failed"}}';
    }}
    location @download_timeout {{
      default_type application/json;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Vary $download_vary always;
      return 504 '{{"detail":"object download timed out"}}';
    }}
    location / {{ return 404; }}
  }}
}}
"""
