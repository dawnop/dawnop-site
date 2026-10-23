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
    # Allow the inner timer to deliver its classified response before outer expiry.
    idle_ms = int(re.match(r"[0-9]+", idle)[0]) * (1 if idle.endswith("ms") else 1000)
    outer_idle = f"{idle_ms + 2000}ms"
    socket_path = root / "object.sock"
    quoted(socket_path)  # Validate before embedding it in a variable proxy URL.
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
    # These policies apply independently to both HTTP request boundaries.
    streaming = """proxy_http_version 1.1;
      proxy_set_header Connection close;
      proxy_pass_request_headers off;
      proxy_pass_request_body off;
      proxy_set_header Content-Length "";
      proxy_ignore_client_abort off;
      proxy_next_upstream off;
      proxy_buffering off;
      proxy_cache off;
      proxy_store off;
      proxy_max_temp_file_size 0;
      proxy_intercept_errors off;
      proxy_redirect off;"""
    hidden = "\n".join(
        f"proxy_hide_header {name};"
        for name in (
            "X-Accel-Redirect",
            "X-Accel-Buffering",
            "X-Accel-Limit-Rate",
            "X-Accel-Expires",
            "X-Accel-Charset",
            "X-Dawn-Download-Target",
            "X-Dawn-Download-Type",
            "Location",
            "WWW-Authenticate",
            "Set-Cookie",
            "Access-Control-Allow-Origin",
            "Access-Control-Allow-Credentials",
            "Vary",
        )
    )
    # X-Accel redirect-copy bypasses outer hide rules. Clean the inner envelope
    # before outer consumes it, preserving these fields only on entity responses.
    clean_maps, clean_headers = [], []
    for name in ("Cache-Control", "Expires", "Content-Disposition", "Accept-Ranges"):
        field = name.lower().replace("-", "_")
        clean_maps.append(
            f'map $object_error_route $clean_{field} {{ "" $upstream_http_{field}; default ""; }}'
        )
        clean_headers.append(
            f"proxy_hide_header {name};\n      add_header {name} $clean_{field} always;"
        )
    maps = "\n  ".join(clean_maps)
    cleaned = "\n      ".join(clean_headers)
    return f"""daemon off;
master_process on;
worker_processes 1;
pid {quoted(root / "nginx.pid")};
error_log /dev/null crit;
events {{ worker_connections 128; }}
http {{
  access_log off;
  client_max_body_size 0;
  # Evaluated only by the inner response header filter, never in rewrite.
  map $upstream_status $object_error_route {{
    default @download_fail_dispatch;
    200 "";
    206 "";
    304 "";
    412 "";
    416 "";
    504 @download_timeout_dispatch;
  }}
  {maps}
  map $status $download_response_type {{
    200 $download_type;
    206 $download_type;
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
      # Freeze FIRST upstream fields before either data-plane hop.
      set $download_target $upstream_http_x_dawn_download_target;
      set $download_type $upstream_http_x_dawn_download_type;
      set $download_origin $upstream_http_access_control_allow_origin;
      set $download_credentials $upstream_http_access_control_allow_credentials;
      set $download_vary $upstream_http_vary;
      if ($download_target = "") {{ return 502; }}
      limit_conn downloads 4;
      limit_conn_status 503;
      proxy_pass "http://unix:{socket_path}:$download_target";
      {streaming}
      proxy_set_header Host local-object.invalid;
      {headers}
      proxy_connect_timeout {idle};
      proxy_read_timeout {outer_idle};
      proxy_send_timeout {outer_idle};
      # Only this hop consumes the trusted inner-generated redirect.
      proxy_ignore_headers X-Accel-Buffering X-Accel-Limit-Rate X-Accel-Expires X-Accel-Charset;
      {hidden}
      proxy_hide_header Content-Type;
      # Only local transport failures use error_page, not object statuses.
      error_page 502 = @download_failure;
      error_page 504 = @download_timeout;
      types {{ }}
      default_type "";
      add_header Content-Type $download_response_type always;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Access-Control-Allow-Credentials $download_credentials always;
      add_header Vary $download_vary always;
      add_header X-Content-Type-Options nosniff always;
    }}
    location @download_fail_dispatch {{
      # special_response clears Content-Type copied by X-Accel before JSON.
      error_page 502 = @download_failure;
      return 502;
    }}
    location @download_timeout_dispatch {{
      error_page 504 = @download_timeout;
      return 504;
    }}
    location @download_failure {{
      default_type application/json;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Access-Control-Allow-Credentials $download_credentials always;
      add_header Vary $download_vary always;
      add_header X-Content-Type-Options nosniff always;
      return 502 '{{"detail":"object download failed"}}';
    }}
    location @download_timeout {{
      default_type application/json;
      add_header Access-Control-Allow-Origin $download_origin always;
      add_header Access-Control-Allow-Credentials $download_credentials always;
      add_header Vary $download_vary always;
      add_header X-Content-Type-Options nosniff always;
      return 504 '{{"detail":"object download timed out"}}';
    }}
    location / {{
      proxy_pass http://127.0.0.1:{application_port};
      proxy_request_buffering off;
      proxy_http_version 1.1;
      proxy_set_header Connection close;
      proxy_set_header X-Dawn-Download-Target "";
      proxy_set_header X-Dawn-Download-Type "";
      proxy_set_header X-Accel-Redirect "";
      proxy_buffering off;
      proxy_next_upstream off;
    }}
  }}
  # Same master/workers, separate HTTP request. A 0700 parent isolates the
  # socket; internal would reject legitimate socket requests too.
  server {{
    listen {quoted("unix:" + str(socket_path))};
    server_name local-object.invalid;
    gzip off;
    send_timeout {idle};
    location / {{
      if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
      proxy_pass https://download_object$request_uri;
      {streaming}
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
      # Ignore AND hide origin controls; only the fixed response map may route.
      proxy_ignore_headers X-Accel-Redirect X-Accel-Buffering X-Accel-Limit-Rate X-Accel-Expires X-Accel-Charset;
      {hidden}
      {cleaned}
      add_header X-Accel-Redirect $object_error_route always;
    }}
  }}
}}
"""
