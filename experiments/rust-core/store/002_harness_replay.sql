-- SPDX-License-Identifier: AGPL-3.0-only
-- Additive disposable reference migration only. Native v1 Store must reject v2.
-- These records are irrevocable denial history, NOT active grants or leases.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE harness_wave_replay (
    session_id BLOB NOT NULL REFERENCES sessions(id),
    project_id BLOB NOT NULL REFERENCES projects(id),
    label TEXT NOT NULL CHECK (
        length(CAST(label AS BLOB)) BETWEEN 1 AND 128
        AND instr(label, char(0)) = 0 AND label NOT GLOB '*[^A-Za-z0-9_.-]*'
    ),
    attempt BLOB NOT NULL CHECK (length(attempt)=32 AND attempt<>zeroblob(32)),
    peer_count INTEGER NOT NULL CHECK (peer_count BETWEEN 1 AND 10),
    PRIMARY KEY (session_id, attempt),
    UNIQUE (session_id, label)
) STRICT;
CREATE TABLE harness_connection_replay (
    session_id BLOB NOT NULL,
    attempt BLOB NOT NULL,
    worker BLOB NOT NULL CHECK (length(worker)=32 AND worker<>zeroblob(32)),
    connection BLOB NOT NULL CHECK (length(connection)=32 AND connection<>zeroblob(32)),
    PRIMARY KEY (session_id, connection),
    UNIQUE (session_id, attempt, worker),
    FOREIGN KEY (session_id, attempt) REFERENCES harness_wave_replay(session_id, attempt)
) STRICT;
CREATE TABLE harness_approval_replay (
    session_id BLOB NOT NULL,
    attempt BLOB NOT NULL,
    worker BLOB NOT NULL,
    request BLOB NOT NULL CHECK (length(request)=32 AND request<>zeroblob(32)),
    action BLOB NOT NULL CHECK (length(action)=32 AND action<>zeroblob(32)),
    PRIMARY KEY (session_id, request),
    UNIQUE (session_id, attempt, worker, action),
    FOREIGN KEY (session_id, attempt, worker)
        REFERENCES harness_connection_replay(session_id, attempt, worker)
) STRICT;
-- Explicit duplicate checks also block INSERT OR REPLACE, even when
-- recursive triggers are disabled. No delete/update API or TTL eviction.
CREATE TRIGGER harness_wave_insert BEFORE INSERT ON harness_wave_replay
WHEN NOT EXISTS (
    SELECT 1 FROM sessions WHERE id=NEW.session_id
    AND project_id=NEW.project_id AND archived=0
) OR EXISTS (
    SELECT 1 FROM harness_wave_replay WHERE session_id=NEW.session_id
    AND (attempt=NEW.attempt OR label=NEW.label)
) OR (SELECT count(*) FROM harness_wave_replay WHERE session_id=NEW.session_id)>=1024
BEGIN SELECT RAISE(ABORT,'wave replay or scope'); END;
CREATE TRIGGER harness_connection_insert BEFORE INSERT ON harness_connection_replay
WHEN NOT EXISTS (SELECT 1 FROM sessions WHERE id=NEW.session_id AND archived=0)
OR EXISTS (
    SELECT 1 FROM harness_connection_replay WHERE session_id=NEW.session_id
    AND (connection=NEW.connection OR (attempt=NEW.attempt AND worker=NEW.worker))
) OR (SELECT count(*) FROM harness_connection_replay WHERE session_id=NEW.session_id)>=1024
OR NOT EXISTS (
    SELECT 1 FROM harness_wave_replay AS w
    WHERE w.session_id=NEW.session_id AND w.attempt=NEW.attempt
    AND (SELECT count(*) FROM harness_connection_replay AS c
         WHERE c.session_id=w.session_id AND c.attempt=w.attempt)<w.peer_count
)
BEGIN SELECT RAISE(ABORT,'connection replay or limit'); END;
CREATE TRIGGER harness_approval_insert BEFORE INSERT ON harness_approval_replay
WHEN NOT EXISTS (SELECT 1 FROM sessions WHERE id=NEW.session_id AND archived=0)
OR EXISTS (
    SELECT 1 FROM harness_approval_replay WHERE session_id=NEW.session_id
    AND (request=NEW.request OR (attempt=NEW.attempt AND worker=NEW.worker AND action=NEW.action))
) OR NOT EXISTS (
    SELECT 1 FROM harness_wave_replay AS w
    WHERE w.session_id=NEW.session_id AND w.attempt=NEW.attempt
    AND (SELECT count(*) FROM harness_connection_replay AS c
         WHERE c.session_id=w.session_id AND c.attempt=w.attempt)=w.peer_count
) OR (SELECT count(*) FROM harness_approval_replay WHERE session_id=NEW.session_id)>=1024
BEGIN SELECT RAISE(ABORT,'approval replay or limit'); END;
CREATE TRIGGER harness_wave_no_update BEFORE UPDATE ON harness_wave_replay
BEGIN SELECT RAISE(ABORT,'immutable replay history'); END;
CREATE TRIGGER harness_wave_no_delete BEFORE DELETE ON harness_wave_replay
BEGIN SELECT RAISE(ABORT,'immutable replay history'); END;
CREATE TRIGGER harness_connection_no_update BEFORE UPDATE ON harness_connection_replay
BEGIN SELECT RAISE(ABORT,'immutable replay history'); END;
CREATE TRIGGER harness_connection_no_delete BEFORE DELETE ON harness_connection_replay
BEGIN SELECT RAISE(ABORT,'immutable replay history'); END;
CREATE TRIGGER harness_approval_no_update BEFORE UPDATE ON harness_approval_replay
BEGIN SELECT RAISE(ABORT,'immutable replay history'); END;
CREATE TRIGGER harness_approval_no_delete BEFORE DELETE ON harness_approval_replay
BEGIN SELECT RAISE(ABORT,'immutable replay history'); END;
CREATE TRIGGER harness_scope_no_reparent BEFORE UPDATE OF project_id ON sessions
WHEN NEW.project_id IS NOT OLD.project_id AND EXISTS (
    SELECT 1 FROM harness_wave_replay WHERE session_id=OLD.id
)
BEGIN SELECT RAISE(ABORT,'immutable harness scope'); END;
-- REPLACE on a parent session could otherwise reparent history without
-- executing UPDATE or recursive DELETE triggers.
CREATE TRIGGER harness_scope_no_replace BEFORE INSERT ON sessions
WHEN EXISTS (SELECT 1 FROM harness_wave_replay WHERE session_id=NEW.id)
BEGIN SELECT RAISE(ABORT,'immutable harness session'); END;
PRAGMA user_version = 2;
COMMIT;
