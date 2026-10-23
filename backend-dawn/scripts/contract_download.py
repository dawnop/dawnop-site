"""Shared isolated nginx and synthetic persisted representations for contracts."""

import hashlib
import importlib.util
import os
import shutil
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

import contract_qiniu_fake


def prepare_rows(db, origin):
    profile = "identity-v1:" + hashlib.sha256(origin.rstrip("/").encode()).hexdigest()
    objects = contract_qiniu_fake.seed_objects()
    with sqlite3.connect(db) as conn:
        names = {row[1] for row in conn.execute("pragma table_info(files)")}
        for name, kind in [
            ("representation_kind", "text"),
            ("representation_key", "text"),
            ("representation_profile", "text"),
            ("representation_etag", "text"),
            ("representation_last_modified_s", "integer"),
        ]:
            if name not in names:
                conn.execute(f"alter table files add column {name} {kind}")
        for key, obj in objects.items():
            size = len(obj["content"])
            tag = '"download-' + hashlib.sha256(obj["content"]).hexdigest() + '"'
            conn.execute(
                "update files set representation_kind=?,representation_key=key,representation_profile=?,representation_etag=?,representation_last_modified_s=1767225600 where is_dir=0 and key=?",
                (
                    "object-identity-v1" if size else "local-empty-v1",
                    profile,
                    tag if size else '"dawn-empty-v1"',
                    key,
                ),
            )


class Proxy:
    def __init__(self, work, application_port, fake_base):
        self.root = Path(work)
        self.root.mkdir(mode=0o700)
        os.chmod(self.root, 0o700)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        spec = importlib.util.spec_from_file_location(
            "download_nginx",
            Path(__file__).parent / "download-transport/nginx_config.py",
        )
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        cert, _, _ = contract_qiniu_fake.tls_material()
        origin = fake_base.replace("http://127.0.0.1", "https://localhost")
        cfg = self.root / "nginx.conf"
        cfg.write_text(
            config.render(
                self.root,
                self.port,
                application_port,
                origin,
                fake_base.split("://", 1)[1],
                cert,
                idle="15s",
            )
        )
        binary = shutil.which("nginx")
        if not binary:
            raise RuntimeError("isolated nginx is required")
        command = [
            binary,
            "-e",
            "/dev/null",
            "-p",
            str(self.root) + "/",
            "-c",
            str(cfg),
        ]
        subprocess.run(command + ["-t"], check=True, capture_output=True)
        self.process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.02)
        self.close()
        raise RuntimeError("isolated nginx failed to start")

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        shutil.rmtree(self.root)
