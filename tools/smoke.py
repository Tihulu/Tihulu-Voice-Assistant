# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""A smoke test for an installed package, away from the source tree.

This is the clean-machine gate: it runs against whatever ``tihulu_jarvis`` is
importable here — the source tree or an installed wheel — with the local state
directories pointed at a private temporary directory. It exercises the public
API the way the client does, and prints one line of evidence at the end. It
imports no window, touches no device and opens no socket.
"""
import os
import sys
import tempfile
from pathlib import Path


def private_home():
    """Point the local state and preference directories at a private place."""
    tmp = Path(tempfile.mkdtemp(prefix="tihulu-smoke-"))
    os.environ["XDG_DATA_HOME"] = str(tmp / "data")
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
    return tmp


def checks():
    from tihulu_jarvis import (a11y, agents, attachments, disclosure, files, keys,
                               profiles, queries, readiness, receipts, settings,
                               states, tasks, transcript, web)
    from tihulu_jarvis.voice import audio, jan, m1, session  # noqa: F401  (import side effects)

    out = []
    # Preferences round-trip, including a refused one.
    cfg = settings.empty()
    cfg["text_scale"] = "large"
    settings.save(cfg)
    out.append(settings.load()["text_scale"] == "large")
    try:
        settings.checked({"text_scale": "huge"})
        out.append(False)
    except m1.Hold:
        out.append(True)

    # A profile is created, activated, escalated and re-read.
    store = profiles.Store(profiles.profile_dir())
    local = profiles.template("private-assistant")
    store.save(local)
    store.save(profiles.template("research-assistant"))
    out.append(store.load("private-assistant").based_on_mode == "LOCAL_ONLY")
    cloud = store.load("research-assistant")
    out.append(bool(profiles.escalation(local, cloud)))
    out.append(profiles.effective(local).profile_id == "private-assistant")

    # The audit trail records one decision and reads it back.
    profiles.record_decision(store.root, "private-assistant", "endpoint",
                             ("http://127.0.0.1:9876/",), "allow", "mode_policy", now=1.0)
    out.append(len(profiles.read_audit(store.root, action="endpoint")) == 1)

    # The state, disclosure and receipt vocabulary is usable.
    data = disclosure.facts(local, "http://127.0.0.1:9876/")
    out.append(states.privacy(data) == states.LOCAL)
    out.append(receipts.is_receipt(receipts.approved("a switch")))
    out.append(a11y.motion_words(a11y.css("large")) == ())
    out.append(agents.TOKENS[0] == "opencode_session_list")
    out.append(keys.names()[0] == "send")
    out.append(tasks.Board().start("turn", "naber nasilsin") == 1)
    out.append(queries.remember((), "kelimem") == ("kelimem",))
    out.append(web.plan(web.parse("https://example.test/docs"))[0] == "GET")
    out.append(files.checked_root(str(Path(tempfile.mkdtemp()))) != "")
    # An empty configuration states the truth: what is ready, what is missing
    # and why, rather than reporting everything as ready because nothing broke.
    cfg["endpoint"] = "http://127.0.0.1:9876/"
    rows = readiness.report(cfg, ("fixture",), True, "private-assistant")
    by_token = {row.token: row for row in rows}
    out.append(by_token["endpoint"].state == readiness.READY)
    out.append(by_token["stt"].detail == "no_model_selected")
    out.append(by_token["local_models"].detail == "fixture")
    out.append(readiness.report({}, (), None, "")[0].state == readiness.MISSING)
    out.append(transcript.render(session.Session().history, started=1.0).endswith("\n"))
    out.append(attachments.MAX_BYTES > 0)
    return out


def main():
    private_home()
    import tihulu_jarvis
    results = checks()
    failed = [index for index, ok in enumerate(results) if not ok]
    print("smoke: %d checks, %d passed, %d failed"
          % (len(results), len(results) - len(failed), len(failed)))
    print("package: %s from %s" % (tihulu_jarvis.__name__,
                                   Path(tihulu_jarvis.__file__).parent))
    if failed:
        print("failed checks:", failed, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
