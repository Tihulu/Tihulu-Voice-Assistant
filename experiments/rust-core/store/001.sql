-- SPDX-License-Identifier: AGPL-3.0-only
-- Experimental schema; never run against the production database.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE projects (
    id BLOB PRIMARY KEY NOT NULL CHECK (length(id) = 16),
    name TEXT NOT NULL CHECK (length(CAST(name AS BLOB)) BETWEEN 1 AND 128)
) STRICT;
CREATE TABLE sessions (
    id BLOB PRIMARY KEY NOT NULL CHECK (length(id) = 16),
    project_id BLOB REFERENCES projects(id),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
) STRICT;
CREATE TABLE messages (
    session_id BLOB NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL CHECK (seq BETWEEN 1 AND 1024),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    body TEXT NOT NULL CHECK (
        length(CAST(body AS BLOB)) BETWEEN 1 AND 16384
        AND instr(body, char(0)) = 0 AND length(trim(body)) > 0
    ),
    PRIMARY KEY (session_id, seq),
    UNIQUE (session_id, seq, role)
) STRICT;
-- client_id is a stable namespace assigned by a trusted owner, never a secret
-- or authorization token. Active grants and process handles are not persisted.
CREATE TABLE turns (
    session_id BLOB NOT NULL REFERENCES sessions(id),
    client_id BLOB NOT NULL CHECK (length(client_id) = 16),
    request_key TEXT NOT NULL CHECK (
        length(CAST(request_key AS BLOB)) BETWEEN 1 AND 128
        AND instr(request_key, char(0)) = 0
        AND request_key NOT GLOB '*[^A-Za-z0-9-]*'
    ),
    user_seq INTEGER NOT NULL,
    user_role TEXT NOT NULL DEFAULT 'user' CHECK (user_role = 'user'),
    assistant_seq INTEGER,
    assistant_role TEXT NOT NULL DEFAULT 'assistant' CHECK (assistant_role = 'assistant'),
    state TEXT NOT NULL CHECK (state IN ('pending', 'cancelled', 'drained', 'completed')),
    PRIMARY KEY (session_id, client_id, request_key),
    UNIQUE (session_id, user_seq),
    UNIQUE (session_id, assistant_seq),
    FOREIGN KEY (session_id, user_seq, user_role)
        REFERENCES messages(session_id, seq, role),
    FOREIGN KEY (session_id, assistant_seq, assistant_role)
        REFERENCES messages(session_id, seq, role),
    CHECK (
        (state = 'completed' AND assistant_seq IS NOT NULL AND assistant_seq = user_seq + 1)
        OR (state <> 'completed' AND assistant_seq IS NULL)
    )
) STRICT;
CREATE UNIQUE INDEX one_live_turn ON turns(session_id)
    WHERE state IN ('pending', 'cancelled');
-- A cancelled turn still occupies the execution slot. A trusted recovery
-- owner must verify drain; a restart alone does not establish that fact.
CREATE TRIGGER valid_turn_transition BEFORE UPDATE ON turns
WHEN NEW.session_id <> OLD.session_id
    OR NEW.client_id <> OLD.client_id
    OR NEW.request_key <> OLD.request_key
    OR NEW.user_seq <> OLD.user_seq
    OR NOT (
        (OLD.state = 'pending' AND NEW.state IN ('cancelled', 'completed'))
        OR (OLD.state = 'cancelled' AND NEW.state = 'drained')
    )
BEGIN
    SELECT RAISE(ABORT, 'invalid turn transition');
END;
CREATE TRIGGER initial_turn_state BEFORE INSERT ON turns
WHEN NEW.state <> 'pending'
BEGIN
    SELECT RAISE(ABORT, 'new turn must be pending');
END;
CREATE TRIGGER no_archive_live BEFORE UPDATE OF archived ON sessions
WHEN NEW.archived = 1 AND EXISTS (
    SELECT 1 FROM turns WHERE session_id = NEW.id AND state IN ('pending', 'cancelled')
)
BEGIN
    SELECT RAISE(ABORT, 'live turn');
END;
CREATE TRIGGER no_turn_in_archive BEFORE INSERT ON turns
WHEN (SELECT archived FROM sessions WHERE id = NEW.session_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'archived session');
END;
PRAGMA user_version = 1;
COMMIT;
