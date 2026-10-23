# Native FM download handoff, K1b

Status: default off, isolated prototype. No production cutover is authorized by
this change. The October 23 coordinator ruling accepts object representation
validators, 206/304/412/416, an actual object HEAD, and progress timeouts in place
of a fixed total download deadline. DAV business routes remain on their existing
implementation. Upload and management curl budgets are unchanged.

## Control plane

Only the native entry reads `DAWN_DOWNLOAD_HANDOFF` (`off` by default, exactly
`on` or `off`) and `DAWN_DOWNLOAD_ORIGIN` (empty by default). These are trusted
deployment settings, never request headers or query parameters. The JVM entry
and existing handler entry points keep the old behavior. The switch requires a
matching, fixed HTTPS `QINIU_DOMAIN`, credentials, and a valid DB key. Missing or
inconsistent configuration returns application JSON 502 without object IO.

`GET` and `HEAD /api/fm/content` authenticate the Bearer token, resolve the DB
row, sanitize its MIME, encode each opaque key segment once, then sign that
encoded URL with the existing Qiniu signing function and expiry. There is no
HEAD, GET, stat or curl preflight. Dot segments, empty segments, backslash,
control characters and oversized keys are rejected. A literal percent is encoded
as `%25`, including a key containing literal `%ZZ`. It is never interpreted as
an incoming escape. Existing preview, download, sign, DAV and upload routes are
unchanged. The feature being off keeps the 355-case golden contract unchanged.

The normal `Response` and `with_header` interface emits an empty control response:

| Header | Meaning |
| --- | --- |
| X-Accel-Redirect | Exact `/_dawn_object` internal location |
| X-Dawn-Download-Target | Encoded, signed origin-form path and query, no authority |
| X-Dawn-Download-Type | Sanitized application MIME |

The control response uses an empty Content-Type through the normal `raw`
Response constructor, so the final MIME appears exactly once. No body source or
HTTP parser changes are involved. This is an explicit site
capability, outside the general `Upstream` effect. Native may finish before the
object transfer; its systemd deadline does not govern the nginx data plane.

## Data plane and field ownership

`backend-dawn/scripts/download-transport/nginx_config.py` generates an entire
isolated stock nginx configuration. It does not include or reload system nginx
configuration. All five nginx temporary path classes and the bootstrap error
log are explicit, including the unused FastCGI/uWSGI/SCGI module defaults. Rootless
protocol acceptance also runs against the extracted Ubuntu nginx 1.24.0 binary.
Its fixed TLS upstream, CA, signing Host and SNI are supplied by
the local harness. The generator intentionally accepts only a loopback connect
endpoint; this is reviewable protocol evidence, not an installer for production.
The operations configuration owner must adapt the template in a later task.

In the internal location's rewrite phase, `set` materializes the first upstream's
MIME, signed target and CORS fields **before** the second proxy runs. Later header
filters use these saved variables, never the now-object `$upstream_http_*` values.
The actual nginx tests check this with conflicting object MIME and CORS.

| Field or decision | Owner |
| --- | --- |
| Bearer authentication, logical row, MIME | Application |
| Object target and expiry | Application signer, fixed configured authority |
| Status, Content-Length, Content-Range | Actual object representation |
| ETag and Last-Modified | Object, used consistently for conditional requests |
| CORS and Vary | Application fields frozen before second proxy |
| Conditional error body MIME | Object representation, not the successful file MIME |
| nosniff | Internal nginx location |
| Body framing, backpressure, cancellation | nginx and object connection |

Only explicitly listed Range and conditional headers reach the object. Other
request headers, application Authorization, cookies, query token and request body
do not. Original client query parameters are never used in the object proxy URL.
The signed target is passed as a variable without URI decode/re-encode. TLS
verification, CA, SNI and Host are explicit. Both data-plane hops disable retries,
connection pools,
redirect following, cache, store and response temporary files. Buffering is off,
`proxy_max_temp_file_size` is zero, and object X-Accel controls are ignored and
hidden. The prototype limits active object transfers to four.

The public internal location proxies the signed target unchanged over a Unix
socket to a second HTTP request in the **same nginx master and workers**. Only
that private server connects to the fixed TLS object authority. The application
handoff protocol has no new fields. No helper service or scripting module runs.

The harness creates its runtime and socket parent with mode 0700, before nginx
starts. The socket is reachable only through this restricted ancestry, even if
nginx creates a more permissive socket inode. Do not bind the inner server to
TCP or move its socket to a shared directory. `internal` cannot authenticate this
second request: it would also reject the legitimate Unix socket request. nginx
owns the socket lifetime; the harness terminates and waits for that instance
before reusing the path, then removes the private tree on success or failure.
A future operations adaptation must create a service-owned restricted directory,
allow only the intended worker identity, and handle startup, drain and removal.

The inner response header filter evaluates `map $upstream_status` with an
explicit allowlist and a default failure route. It emits
`X-Accel-Redirect $object_error_route always` only for failure. The outer proxy
consumes this trusted header and dispatches to fixed local JSON locations.
Never read/cache this map in rewrite: the current object response does not yet
exist. `always` is necessary for all final response statuses. The inner proxy
both ignores and hides every origin X-Accel header, so the object cannot choose
an internal route, buffering, charset or rate. The outer alone consumes the
generated route. Retries and inner error interception remain off, so the inner
status variable describes one object attempt.

X-Accel has a special header-copy path that bypasses ordinary outer hide rules.
The inner suppresses Set-Cookie and challenges and conditionally restores
Cache-Control, Expires, Content-Disposition and Accept-Ranges only on allowed
entity responses. The outer dispatch first generates a local special response
to clear a copied Content-Type, then emits a single JSON MIME and local length.
ETag, Last-Modified, Content-Range and origin body bytes cannot enter that JSON
response. Application CORS, credentials, Vary and nosniff are restored locally.

Both hops have explicit connect, read idle, send idle and downstream write idle
budgets. The outer read/send budget is the inner idle budget plus two seconds,
allowing a classified inner timeout to arrive before the outer timer expires.
The harness uses one-second idle budgets to make failure tests
bounded, and a two-second progress case exceeds a one-second former total budget.
There is no fixed total timer for a progressing download. Client cancellation
must close the source connection, including after native has exited.

## Status and error contract

| Trigger | Feature off | Candidate |
| --- | --- | --- |
| Invalid application credentials | Application 401, no object | Same |
| Missing row/key | Application 404 | Same |
| Missing/mismatched handoff configuration | Not used | Application JSON 502, no object |
| Successful GET | Staged native 200 | One object GET, streamed 200 or 206 |
| HEAD | Native GET followed by body suppression | One actual object HEAD, no body |
| Object empty representation | Object fetch | Object GET/HEAD with zero length |
| Bounded/open/suffix Range | FM ignores Range | Entity server handles it |
| Valid unsatisfied Range | FM ignores Range | 416, object Content-Range |
| Invalid or multiple Range | FM ignores Range | Fake source policy: full 200 |
| Object ignores Range | 200 | Full 200; no force_ranges |
| Matching If-None-Match or date | FM ignores condition | Entity 304 |
| Failed If-Match or If-Unmodified-Since | FM ignores condition | Entity 412 |
| If-Range mismatch or weak tag | FM ignores condition | Full representation 200 |
| File response | Staged 200 | Only 200 and 206 are file success |
| Unexpected 2xx, redirects, 401/408/444/499/612/999 and other final errors | Application 502 | Narrow local JSON 502, no object challenge or body |
| Object 504 | Application 502/504 | Narrow local JSON 504 |
| Connect/TLS/header timeout | Application 502/504 | Narrow JSON 502/504 |
| Short body or stall after final head | Stage can reject before serving | Incomplete transfer, no second head or appended error JSON |

The narrow JSON body is `{"detail":"object download failed"}` or
`{"detail":"object download timed out"}`; it intentionally omits the previous
curl exception text and raw source status. Error MIME must be JSON. HEAD errors
have no body. Source Location and ordinary source authentication fields must not
escape. An origin 408 is a received object response and maps to 502; it is
separate from an actual upstream connect/response-header timeout, which maps to
504. 304/412/416 retain their object entity semantics and conditional body MIME.
Other unexpected 2xx, including 201 and 204, are failures. The guarantee covers
final statuses before client headers commit. A short body or stalled stream
following a committed 200/206 terminates that transfer, without JSON, another
response, retry or replay.

K1's single-hop interception leaked 401 challenges, disconnected on 408 and
passed 612 through because `error_page` cannot match it. K1b keeps that parser
boundary check as evidence and enforces the contract through the response map.
No source status needs to appear in an `error_page` list. Application 401,
external internal-location 404 and local concurrency-limit 503 remain separate.
The summary derives the error-contract gate from the measured GET/HEAD matrix,
and derives production blockers from individual gate outcomes. Passing the
isolated matrix does not complete DAV integration or authorize production.

## DAV follow-up

The entity tests exercise the next DAV contract, not a migrated DAV business
route or a real Finder/Windows client. The fixture deliberately stores DB size 7,
a logical key and DB time that disagree with actual object size, ETag and date.
Candidate GET/HEAD must use actual object metadata. Current DAV HEAD and PROPFIND
continue to use the old logical metadata until knife 2.

Knife 2 must add representation ETag and Last-Modified fields on registration,
write and copy, backfill old rows, and switch GET/HEAD/PROPFIND together. Additive
fields must be ignored by old applications. PROPFIND must read persisted entity
metadata without a per-child HEAD. Real Qiniu stat hash/putTime equivalence with
the configured download representation remains unverified; a fake cannot prove
it. No production scan or metadata migration is performed here. The local empty
DAV branch must keep GET/HEAD/PROPFIND consistent.

The harness also exercises the rejected legacy workaround in both uncacheable
and explicitly cacheable configuration copies. With cache disabled nginx skips
its local not-modified filter; in the cacheable control the old matching tag
still yields 412. This distinction does not make mixed validators safe for
If-Range. In particular, removing outgoing
conditions and adding the logical ETag at the end does not remove the client's
conditions from nginx's local not-modified filter. It is not an acceptable
compatibility mechanism.

## Reproduce and release gates

Run `backend-dawn/scripts/download-transport/run.sh --work <empty-directory>` for
protocol, genuine configuration mutations, cancellation and TLS proof. Use
`--nginx <absolute-binary>` to replicate it with another stock build. Acceptance
covers stock nginx 1.24.0 and 1.31.3; CI runs full measurement on the extracted
Ubuntu binary and protocol/negative controls on checksum-pinned 1.31.3 built in
a private prefix. Add
`--full` after building the JVM jar for ten interleaved rounds per arm in each
cold and warm group, and
256/512 MiB digests with one and four slow clients. `--binary` can reuse an
explicitly built native ELF while iterating; the default builds current sources.
All three timing arms pass through the same nginx and TLS fake source. Cold
means a fresh JVM for each measured request; native is a fresh process for every
request in both groups. nginx remains resident, TLS sessions are not reused and
host page caches are not flushed. Final-status first-byte and first-body-byte
clocks are separate. The output directory retains only `summary.json`; generated keys, certificates,
configurations, signed request targets and per-process traces are deleted.

Performance gates are fixed in `GATES` before execution: candidate body TTFB p95
at most max(250 ms, JVM p95 + 100 ms), at most stage p95 / 4, temporary disk at most
1 MiB per request with no body staging, and 256 to 512 MiB transport memory growth
at most 16 MiB per request. Source generation memory is listed separately; nginx
baseline and its workers are included. Both HTTP hops, their socket descriptors
and both Unix socket endpoints are counted in that same process tree. The Unix
endpoint-count assertion must see both endpoints per active request. Temporary
file scans include unlinked open descriptors. Socket fd counts do not measure
kernel buffer memory; shared sockstat/cgroup counters remain context only,
not isolated cgroup evidence. S8 upload memory and budget gates remain unchanged.

Every negative control modifies real code or a full nginx configuration copy:
stage restoration, internal removal, credential forwarding, actual buffered disk
writes, lost Range, mixed validators, object 401 served as a successful file, and
ignored client cancellation. Each must trip its corresponding semantic gate.
Four further real configuration controls remove the generated header's `always`,
ignore the trusted route at the outer hop, trust origin X-Accel at the inner hop,
and evaluate the response map prematurely in rewrite. The first two must break
401/408/612 normalization, the latter two must break valid object downloads.
Restored configuration then runs the positive matrix. Existing repository gates,
355 dual-arm contracts and named skips remain required.

The feature remains off pending complete DAV metadata integration, real origin
validator/backfill evidence, real clients, deployment resource limits and a
separately authorized S13b cutover. The extra HTTP parsing, socket buffers and
body copy are measured costs; K1 timing/resource results cannot stand in for K1b.
Production download is not released by this prototype.

Recorded local acceptance is available as the sanitized
[nginx 1.31.3 full summary](evidence/download-k1b-nginx-1.31.3-full.json) and
[nginx 1.24.0 protocol summary](evidence/download-k1b-nginx-1.24.0-protocol.json).
Both have 93 passing protocol cases, including 54 error GET/HEAD cases, and all
12 required negative controls. The full run measured candidate cold/warm body
TTFB p95 at 108.56/114.02 ms. Single-client PSS growth was -770 KiB and four-client
growth was 574.5 KiB per request; negative growth is sampling/shared-page noise.
All four resource groups wrote zero temporary body bytes. Both Unix endpoints
per transfer were observed. The error, performance and resource gates pass;
production remains `BLOCKED` by the separately listed pending gates.

Mechanism references: [nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[internal locations](https://nginx.org/en/docs/http/ngx_http_core_module.html#internal),
and the [nginx conditional filter](https://github.com/nginx/nginx/blob/master/src/http/modules/ngx_http_not_modified_filter_module.c).
