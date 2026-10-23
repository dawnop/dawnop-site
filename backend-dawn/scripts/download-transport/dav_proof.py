"""Migration model only, not the production DAV router or a Qiniu stat oracle."""

import sqlite3
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


def prove(path, get, head, source_count):
    fields = lambda r: {k.lower(): v for k, v in r["headers"]}  # noqa: E731
    entity = fields(get)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE representations (path TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, size INTEGER)"
        )
        db.execute(
            "INSERT INTO representations VALUES (?, ?, ?, ?)",
            ("payload", entity["etag"], entity["last-modified"], get["bytes"]),
        )
        before = source_count()
        etag, modified, size = db.execute(
            "SELECT etag, last_modified, size FROM representations WHERE path='payload'"
        ).fetchone()
        # Future PROPFIND reads persisted representation fields. No per-item IO.
        xml = ET.fromstring(
            '<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:getetag>'
            + escape(etag)
            + "</D:getetag><D:getlastmodified>"
            + escape(modified)
            + "</D:getlastmodified><D:getcontentlength>"
            + str(size)
            + "</D:getcontentlength></D:prop></D:propstat></D:response></D:multistatus>"
        )
        assert (
            xml.findtext(".//{DAV:}getetag") == fields(head)["etag"] == entity["etag"]
        )
        assert (
            xml.findtext(".//{DAV:}getlastmodified")
            == fields(head)["last-modified"]
            == entity["last-modified"]
        )
        assert (
            xml.findtext(".//{DAV:}getcontentlength")
            == fields(head)["content-length"]
            == str(get["bytes"])
        )
        assert source_count() == before
    return {
        "persisted_model": "GET/HEAD/PROPFIND agree",
        "propfind_object_requests": 0,
        "production_DAV": "unchanged",
        "stat_equivalence": "not proved by fake metadata",
    }
