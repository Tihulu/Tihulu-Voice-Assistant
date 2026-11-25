# Integration readiness and acceptance handoff

This is a maintainer-authored experiment contract, not an independent review or
authorization to activate any new service. The production review target remains
#128, commit 9f9f26344409a7727af6e62937c4e2e9e44c5089. The experiment baseline
remains #118 d4b756351b1552aa5806c89e39eda3b5b82aa337. Keep them unchanged.

## Boundary map

| Area | Experimental source | Existing shipped integration surface (not modified) | Required real acceptance |
| --- | --- | --- | --- |
| Chat-first lifecycle | presenter.py, workspace.py, conversation.py | desktop.py, states.py, chat_files.py, attachments.py | Native widget ownership, main-loop callback generation, picker cancellation and retained state |
| Rich results | rich_chat.py, research_board.py | analysis_controller.py, analysis_ui.py, analysis_export.py, temperature_engine.py | Native table/texture/export, explicit attachment consent, real selected-file identity |
| Local model and voice | jan_replay.py, voice_pipeline.py, voice_turn.py, inference.py | voice/jan.py, voice/audio.py, voice/session.py, model_discovery.py, resources.py | Real supported Jan, Whisper, Piper; TR/EN/DE, measured resource use and process drain |
| Policy and provider | gateway.py, session_controller.py, provider_session.py | profiles.py, routing.py, disclosure.py, keys.py, receipts.py | Saved-profile binding, trusted actor/context, atomic effect broker, approved official account flows, OS-only elevation |
| Research/browser | research_transport.py, browser_fixture.py, research_board.py | web.py, catalog_fetch.py, files.py, search.py | Explicit public egress, actual DNS/peer validation, isolated Firefox request enforcement, no private data |
| Agents/tasks | task_board.py, connector_protocol.py, scheduler.py, task_chain.py | agents.py, tasks.py, local_actions.py, owned_wait.py | Actual OpenCode/desktop adapter protocol, local executor, resource ownership and cancellation |
| Market/release | inventory.py, market_fixture.py, version_book.py, release_rehearsal.py, release_preflight.py | market.py, transfer_http.py, settings.py, readiness.py, packaging/deb | Verified packages, durable registry, recovery from failure, clean target-machine installation and rollback |

## Integration sequence, subject to explicit authorization

1. Independent review of the frozen production candidate, separate from this
   maintainer's evidence; use #116 and reviewer-owned evidence branch.
2. Owner P1/P2/P3 preview on the exact candidate using #129's isolated
   rehearsal instructions. Record sanitized PASS/FAIL/HOLD only.
3. Review the complete experimental candidate and shared R3 boundary. A green
   Python fixture suite is not proof of a trusted actor, OS isolation, transport,
   credentials, or device behavior.
4. If separately approved, create a new integration candidate, preserve the
   stable version and frozen review trees, bind the real saved profile and
   explicit approval to exact effects. Start with local-only chat/results.
5. Review native GTK event ownership and physical behavior before enabling
   local processes. Test cancellation, resource drain, stale callback refusal,
   and expiry before/after effects using independently controlled evidence.
6. Add provider, browser and host adapters only under their own explicit gates.
   Secure Hybrid primary planning/execution stays local; cloud consultation
   requires separate account/model/payload consent and minimized fields.
7. Separate owner clean-install/uninstall/rollback and explicit release signoff.

## Acceptance truth

The historical 35-gate rubric (#79, comment 7135161613 and subsequent accepted
ledger) currently records 13 accepted, 14 gates with relevant experimental
precursors, and eight without a real acceptance result. The latter are three
physical R0/R1 gates, R2/R3/R4 integration acceptance, R5 clean target-machine
install/rollback and release acceptance. The in-memory release rehearsal
supports preparation only. None of these gates can be closed by a fixture
count. No actual GTK changes, live provider login, network access, shell,
execution, installation, merge or release is authorized by this document.

## Required negative probes before any real adapter activation

- Same-named independent sessions cannot reuse a prior unused turn, source,
  export or attachment permission; generation switch removes retained content.
- Revoke or expire during approval, initiation, stream and final publication;
  prove no unauthorized real initiation and no stale visible result.
- Stop and close must drain actual owned processes and resource leases; a
  delayed completion cannot mutate a replacement UI.
- Secure Hybrid cloud consultation cannot replace the primary local planner.
  Account, origin, model, payload and destination must all match approval.
- No raw selected files, transcripts, paths, credentials or terminal output
  may enter a cloud consultation; verify actual bytes at the broker boundary.
- Prove symlink/special-file/changed-inode rejection on actual selected data;
  distinguish fixture symbolic slots from a filesystem security guarantee.
- Browser policy must be enforced on the real connection and all subrequests,
  not merely on a simulated response.
- Failure during real update/rollback must preserve stable and shared assets,
  including on process interruption; in-memory compare-and-swap is insufficient.

No independent PASS is implied. No permission expansion or installation.
