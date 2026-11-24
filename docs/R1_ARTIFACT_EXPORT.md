# R1 artifact export and statistics table

Stacked on #85. Adds a native statistics grid and explicit Export last result control. Four formats: exact generated SVG, PNG encoded by the trusted GDK texture encoder, normalized numeric series CSV, statistics CSV. CSV is regenerated from validated binary64 numbers, not a byte-for-byte copy of the input. There is no model-generated file content, automatic upload, source filename, private path or chat history in the exported payload. The current revision is selected when the save dialog opens.

The trusted local save dialog grants one destination. Active LOCAL_ONLY profile, version/context, artifact-read authorization, and filesystem_write/csv_analysis_export policy are checked; explicit deny or ask is not overridden. No path can come from a model proposal. The Linux writer uses descriptor-relative traversal, rejects symlink components, requires an owner-controlled final directory without group/other write mode bits, and uses a private 0600 temporary file and atomic no-replace hard link. Existing entries, including symlinks, are not overwritten even if a GTK dialog suggests replacement. No directories created. Filesystems without the required link support fail closed. Authorization is checked before I/O and final publication. The bounded save runs on the GTK thread so in-app context transitions do not interleave with commit; a slow filesystem may block the UI. Not an OS sandbox, mount isolation or crash-durability guarantee. A process crash may leave a private temporary file. Exported files remain under the owner's control and cannot be recalled by session revocation.

Tests: 14 core fixtures (exact content, numeric units, digest, destination bounds, existing files, symlinks, private mode, revocation and cleanup), plus five GTK fixtures for native PNG/SVG/CSV output, statistics grid, stale/denied save, and dialog cancel/reopen. Exact-head CI must be verified. No physical device, real Jan service or interactive portal acceptance claim.

## R1 owner acceptance after CI and independent review

Use a disposable profile/worktree and a synthetic five-row temperature CSV. Record OS/GTK versions, commit, exact expected/observed results and PASS/FAIL/HOLD, without private paths or credentials.

1. Launch without Jan; select the CSV via the actual native picker. Check 5 samples, mean 24, min 20, max 28, change 8, duration 40, actual chart and task board.
2. Type a supported local command, select a synthetic file, and verify that the correct local voice speaks the computed result. Recolor in TR/EN/DE; confirm data/lineage unchanged.
3. Connect verified local Jan, then use an explicit Task:/Görev:/Aufgabe: request. Verify the selected endpoint is truly local and the classifier sees only the submitted instruction. Test invalid proposals and refusal.
4. Use the physical microphone with Whisper; verify real TR/EN/DE transcription and Piper playback, mute, stop-speaking, cancel and hard stop. Verify that unrelated processes survive.
5. Export SVG, PNG, numeric CSV and statistics CSV to a new private folder. Check content, permissions and refusal to replace an existing file. Cancel/reopen both open and save pickers and test close while a dialog is open.
6. Exercise profile switch and end conversation; verify old artifact controls are disabled. Test explicit file-read/write denial and no local analysis from cloud modes.
7. Investigate the previously reported COSMIC picker crash separately using sanitized trace/backtrace and reproducible steps; root cause remains UNKNOWN until evidenced. Record actual clean install and rollback separately.

Do not mark R1 owner verified or release ready from synthetic CI alone. No automatic merge.
