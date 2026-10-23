# Native FM download handoff, knife 1

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
configuration. Its fixed TLS upstream, CA, signing Host and SNI are supplied by
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
verification, CA, SNI and Host are explicit. There are no retries, connection pool,
redirect following, cache, store or response temporary files. Buffering is off,
`proxy_max_temp_file_size` is zero, and object X-Accel controls are ignored and
hidden. The prototype limits active object transfers to four.

Connect, upstream read idle, upstream send idle and downstream write idle budgets
are explicit. The harness uses one-second idle budgets to make failure tests
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
| Object redirects or ordinary errors | Application 502 | Narrow static JSON 502; source 504 maps 504 |
| Connect/TLS/header timeout | Application 502/504 | Narrow JSON 502/504 |
| Short body or stall after final head | Stage can reject before serving | Incomplete transfer, no second head or appended error JSON |

The narrow JSON body is `{"detail":"object download failed"}` or
`{"detail":"object download timed out"}`; it intentionally omits the previous
curl exception text and raw source status. Error MIME must be JSON. HEAD errors
have no body. Source Location and ordinary source authentication fields must not
escape. The acceptance summary records concrete stock nginx gaps separately;
passing the expected-gap assertions never grants production acceptance.

Stock `error_page` permits 300..599 except reserved 499, so it cannot express the
required mapping of source 612, other nonstandard statuses outside that interval,
or reserved 499. Listing all representable errors does not solve those cases.
The real `nginx -t` rejection and actual 612 response are retained as counts and
status evidence. The isolated matrix also finds that nginx copies an object 401
WWW-Authenticate challenge into its error response even when that header is
hidden on the normal proxy path, and treats source 408 as a pre-response
connection close. These are additional concrete failures of the narrow error
contract. Conditional 412/416 preserve the source body MIME; successful file MIME
continues to come only from the application. No njs, Lua, headers-more or helper is introduced to hide this.

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
protocol, genuine configuration mutations, cancellation and TLS proof. Add
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
baseline and its workers are included. Shared host kernel counters are context,
not isolated cgroup evidence. S8 upload memory and budget gates remain unchanged.

Every negative control modifies real code or a full nginx configuration copy:
stage restoration, internal removal, credential forwarding, actual buffered disk
writes, lost Range, mixed validators, object 401 served as a successful file, and
ignored client cancellation. Each must trip its corresponding semantic gate.
Restored configuration then runs the positive matrix. Existing repository gates,
355 dual-arm contracts and named skips remain required.

The feature remains off pending the concrete error gaps, complete DAV metadata
integration and a separately authorized S13b cutover. Production download is not
released by this prototype.

Mechanism references: [nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[internal locations](https://nginx.org/en/docs/http/ngx_http_core_module.html#internal),
and the [nginx conditional filter](https://github.com/nginx/nginx/blob/master/src/http/modules/ngx_http_not_modified_filter_module.c).
