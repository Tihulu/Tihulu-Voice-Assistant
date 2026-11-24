# R2 implementation contract and acceptance fixtures

Design only. Companion to R2_RESEARCH_RICH_CHAT_DESIGN.md; subject to PRODUCT_SCOPE.md and RECOVERY_ROADMAP.md. R1 acceptance #90 remains the implementation gate. No new runtime permissions, network calls, downloads, dependencies or executable code in this document.

## 1. Smallest useful end-to-end slice

The first implemented R2 feature must read an explicitly selected public HTTPS URL, produce a bounded source snapshot, and show a locally grounded answer in native chat with its clickable source reference. If no verified model is available, display the extracted source preview and state that synthesis is unavailable. Do not fake a search result or pretend a preview is an AI answer. Actual browser research, including JS-capable pages and user-approved screenshots, remains required by PRODUCT_SCOPE.md, but is a separate adapter gate. A direct text fetch is not a replacement for Firefox. Optional background research is retained for an explicitly started, owned, bounded job; no always-on crawler.

## 2. Proposed versioned interfaces

Names and shapes are design contracts, not current APIs. Use immutable typed records, strict field validation and bounded JSON only at untrusted input boundaries.

| Record | Required fields and ownership |
|---|---|
| ResearchContext | session_id, task_id, epoch, profile_id, profile_version, policy_generation, inference_route_id; trusted controller generates identities |
| ResearchRequestV1 | kind (read_url or search_query), selected URL or explicit query, language (en/tr/de), adapter_id, requested limits; trusted UI accepts and normalizes |
| ResearchGrant | context identity, action, destination and normalized scope, expiration, revocation generation; trusted UI/policy only, never returned by model |
| SourceSnapshotV1 | source_id, task/context identity, final canonical URL, title, retrieved_at_utc, MIME, digest, bounded extracted text, truncation flags, adapter version |
| EvidenceSpan | source_id, start/end offsets in the exact immutable extracted text; validated against snapshot |
| ResearchResultV1 | artifact_id, parent_id if derived, language, ordered typed blocks, source IDs, partial/refusal reasons, engine/version |
| TaskEvent | task_id, epoch, stage, safe progress counters, bounded reason code; no raw URL query, payload, secret or private path in telemetry |

Initial hard ceilings: 3 sources, 3 redirects/source, 512 KiB decoded response/source, 20,000 extracted characters/source, 60 seconds total. Also cap encoded bytes and headers separately. Query max 512 characters, URL max 2,048 characters; no control characters. Start with no query strings on direct-read URLs; provider-specific search parameters built from the exact user-confirmed query only. Scope matches canonical origin plus path segments, not naive string-prefix matching. A candidate URL supplied by a model is not an accepted URL or a grant.

Model-facing output accepts a strict structured proposal of paragraphs or tables with bounded text and citations (opaque source_id + optional evidence span). No URL, HTML, script, actions, file writes, shell, credentials or extra keys. Reject duplicate keys, malformed data, unknown IDs, invalid span offsets and oversized output. Renderer resolves clickable links exclusively from the trusted store. A valid span proves where evidence came from, not that a generated assertion is entailed by it; mark summaries as model-generated, allow source inspection, and show unverified claims as such. No automatic verification of semantic truth is implied.

Example synthetic source (documentation fixture only; .invalid domains must not be fetched):

- Source S1, title "Fixture A", text "Sample A measured 24 degrees Celsius."
- Source S2, title "Fixture B", text "Sample A measured 26 degrees Celsius."
- Valid report: "Fixture A reports 24 °C [S1]; Fixture B reports 26 °C [S2]. The reported results differ." Citation IDs resolve to the actual two fixture records.
- Invalid report: any invented S3, claimed consensus, arbitrary clickable URL, HTML action or request to disclose private context. Reject invalid references; do not turn generated disagreement into a single asserted true value.

## 3. Execution and user-visible state

IDLE -> awaiting_approval -> resolving -> fetching -> extracting -> synthesizing -> validating -> published. Terminal outcomes: denied, cancelled, failed, partial. A partial result may include successfully validated sources only, explicitly stating missing sources and reasons. At any async boundary, check task ownership, context/epoch, route, live policy and deadline before continuing. Recheck grant immediately before network connection and before each redirect and publication. No late event can resurrect a cancelled result; no auto-resume after mode change.

Stop Speaking affects only owned audio. Cancel stops the current research job and drains its owned connections/workers. Hard Stop revokes temporary grants and invalidates pending actions and unpublished results. End Conversation clears context according to retention policy. Previously exported files and already disclosed bytes cannot be revoked retroactively. No automatic access to R1 artifacts. A user-approved public search query is the only outbound search content; no implicit conversation history. Local synthesis receives only the authorized query and approved public excerpts. Keep direct fetch transport separate from model provider transport and browser traffic.

## 4. Concrete acceptance matrix

| ID | Setup / action | Observable required outcome |
|---|---|---|
| R2-P01 | Deny, ASK without consent, expired grant, unknown mode, strict offline | zero DNS/network calls and zero model calls for research; bounded refusal |
| R2-P02 | Change profile/epoch while resolving or fetching | owned operation cancelled; no new connection or stale publication |
| R2-P03 | Approve URL /news; request /newsletter or encoded traversal | refusal, zero unapproved request bytes |
| R2-P04 | Model asks for POST, file read, shell or arbitrary URL | no action or grant; strict proposal refused |
| R2-N01 | DNS result contains loopback/private/mapped/non-global or mixed addresses | no connection |
| R2-N02 | DNS changes after validation | dial only the validated pinned public address or refuse; TLS name validation remains correct |
| R2-N03 | Redirect to other origin, local host, non-HTTPS, nonstandard port or unapproved path | no follow-up request without a separately allowed scope |
| R2-N04 | Bad TLS identity; malformed framing; oversized headers/body/decompressed output | fail closed, bounded resources, no accepted source |
| R2-N05 | Environment proxy set; hostile embedded image/script/form | no proxy bypass or subresource traffic in direct adapter |
| R2-C01 | HTML instructs model to expose a fake private-history canary | no canary in transport or report; zero unapproved actions |
| R2-C02 | Invented source ID, URL or invalid span in model result | invalid result refused, only real retrieved source cards |
| R2-C03 | S1/S2 disagree or one is truncated/unavailable | preserve conflict or limitation; no fabricated citation |
| R2-C04 | Follow-up asks for existing public sources; then switch route | same-context references retained; cross-route replay refused |
| R2-X01 | Export report to new selected private destination, then repeat | correct bounded content and source manifest; no overwrite |
| R2-X02 | Cancel during fetch, synthesis, export or audio | no stale output; only owned jobs stopped; previously committed results preserved |
| R2-B01 | Firefox installed but isolation not verified | detected/compatible distinct from available; launch denied, no fallback to personal profile |
| R2-B02 | Approved isolated Firefox page uses JS/subresource to contact local listener | egress enforcement blocks unauthorized request; capture listener count and browser status |
| R2-B03 | Personal Firefox already open with synthetic cookie | separate session; cookie absent from owned session and output; personal process survives cancel |
| R2-B04 | Actual approved JS page and screenshot request in isolated browser | content displayed/extracted, source provenance retained, screenshot only explicitly authorized and locally stored; HOLD if unsupported |
| R2-O01 | Actual TR/EN/DE research, citations, follow-up, local voice and keyboard navigation | owner evidence with exact commit and OS/browser versions, no personal data |

Use fake resolver/transport and controlled test listeners only through test injection; never relax the production private-address policy for a local fixture. Verify actual outbound bytes, process ownership, exit codes and bounded duration. Model mocks cannot certify real Firefox isolation. Browser API/driver compatibility and current provider terms require verified documentation before implementation. R2 runtime starts only after R1's independent and owner gates. No public release or merge authorized.

