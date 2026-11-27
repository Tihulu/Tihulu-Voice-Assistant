-- SPDX-License-Identifier: AGPL-3.0-only
-- Disposable fixture v3, never a production migration.
-- One unresolved admission per session. No restart-based release.
BEGIN IMMEDIATE;
CREATE TABLE harness_recovery (
    session_id BLOB NOT NULL,
    attempt BLOB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('unresolved','drained')),
    PRIMARY KEY (session_id, attempt),
    FOREIGN KEY (session_id, attempt)
        REFERENCES harness_wave_replay(session_id, attempt)
) STRICT;
CREATE UNIQUE INDEX one_unresolved_harness ON harness_recovery(session_id)
    WHERE state='unresolved';
CREATE TRIGGER harness_recovery_initial BEFORE INSERT ON harness_recovery
WHEN NEW.state<>'unresolved' OR EXISTS (
    SELECT 1 FROM harness_recovery WHERE session_id=NEW.session_id
    AND (attempt=NEW.attempt OR state='unresolved')
)
BEGIN SELECT RAISE(ABORT,'unresolved or repeated admission'); END;
CREATE TRIGGER harness_wave_recovery AFTER INSERT ON harness_wave_replay
BEGIN
    INSERT INTO harness_recovery VALUES (NEW.session_id,NEW.attempt,'unresolved');
END;
CREATE TRIGGER harness_recovery_transition BEFORE UPDATE ON harness_recovery
WHEN NEW.session_id IS NOT OLD.session_id OR NEW.attempt IS NOT OLD.attempt
OR OLD.state<>'unresolved' OR NEW.state<>'drained'
OR NOT EXISTS (
    SELECT 1 FROM harness_wave_replay w
    WHERE w.session_id=OLD.session_id AND w.attempt=OLD.attempt
    AND w.peer_count=(SELECT count(*) FROM harness_connection_replay c
        WHERE c.session_id=w.session_id AND c.attempt=w.attempt)
)
BEGIN SELECT RAISE(ABORT,'invalid recovery transition'); END;
CREATE TRIGGER harness_recovery_no_delete BEFORE DELETE ON harness_recovery
BEGIN SELECT RAISE(ABORT,'immutable recovery history'); END;
CREATE TRIGGER harness_recovery_no_archive BEFORE UPDATE OF archived ON sessions
WHEN NEW.archived=1 AND EXISTS (
    SELECT 1 FROM harness_recovery WHERE session_id=NEW.id AND state='unresolved'
)
BEGIN SELECT RAISE(ABORT,'unresolved harness'); END;
CREATE TRIGGER harness_recovery_no_late_approval BEFORE INSERT ON harness_approval_replay
WHEN NOT EXISTS (
    SELECT 1 FROM harness_recovery WHERE session_id=NEW.session_id
    AND attempt=NEW.attempt AND state='unresolved'
)
BEGIN SELECT RAISE(ABORT,'inactive harness'); END;
PRAGMA user_version = 3;
COMMIT;
