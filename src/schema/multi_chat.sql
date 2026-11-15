-- dd-chat database schema (SQLite): the file multi_chat.db under ~/.dd-chat, or $DD_CHAT_HOME.
--
-- Reference copy for reading and tooling. The app does not run this file: chat_db.py creates the same tables from its SCHEMA string
-- (the messages columns come from chat_db.COLUMNS) and adds missing columns to older databases with ALTER TABLE.
-- tests/test_chat_db.py::test_schema_file_matches_code checks that the two agree; change both together.
--
-- Timestamps are ISO 8601 text in local time, to the second (e.g. 2026-11-15T17:53:12).
-- disabled = 1 is a soft delete: the row is hidden from the lists but kept.
-- Foreign keys are enforced per connection (PRAGMA foreign_keys = ON in chat_db.connect).

-- One JSON value per key: Chats-page widgets (multi_chat_settings), Settings-page model choices (chat_models), presets.
CREATE TABLE IF NOT EXISTS settings (
  key       TEXT PRIMARY KEY,
  value     TEXT NOT NULL                                 -- JSON
);

-- A chat: one or more chatbots answering the same questions.
CREATE TABLE IF NOT EXISTS sessions (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  title     TEXT NOT NULL,                                -- the first question, up to 80 characters
  created   TEXT NOT NULL,
  updated   TEXT NOT NULL,
  disabled  INTEGER NOT NULL DEFAULT 0,                   -- 1: soft-deleted (Review page)
  rev       INTEGER NOT NULL DEFAULT 0                    -- bumped by every save; a stale tab forks a new session instead of overwriting
);

-- One row per message; bot = chatbot number - 1, pos = the message's place in that chatbot's history.
CREATE TABLE IF NOT EXISTS messages (
  session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  bot           INTEGER NOT NULL,
  pos           INTEGER NOT NULL,
  role          TEXT NOT NULL,                            -- user | assistant
  content       TEXT NOT NULL,                            -- the text sent to / received from the model
  shown         TEXT,                                     -- the question as the user typed it, when it differs from content
  q             INTEGER,                                  -- question number within the session
  model         TEXT,                                     -- <graflang adapter>:<model>
  seconds       REAL,
  feedback      INTEGER DEFAULT 0,                        -- 1 thumbs up, -1 thumbs down, 0 none
  est_tokens    INTEGER,
  ctx_tokens    INTEGER,
  language      TEXT,
  links         TEXT,                                     -- JSON: the answer's URLs and their check results
  input_tokens  INTEGER,
  output_tokens INTEGER,
  cost_usd      REAL,
  best          INTEGER DEFAULT 0,                        -- 1: picked as the best answer to its question
  shared_from   INTEGER,                                  -- the chatbot this answer was copied from (identical input asked once)
  temperature   REAL,
  instructions  TEXT,
  PRIMARY KEY (session_id, bot, pos)
);

-- A model's comparison of the answers to question q.
CREATE TABLE IF NOT EXISTS judgements (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  q          INTEGER NOT NULL,
  judge      TEXT,                                        -- the comparing model
  content    TEXT NOT NULL,
  seconds    REAL,
  PRIMARY KEY (session_id, q)
);

-- Notes page.
CREATE TABLE IF NOT EXISTS notes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT '',                   -- Markdown
  created     TEXT NOT NULL,
  updated     TEXT NOT NULL,
  disabled    INTEGER NOT NULL DEFAULT 0                  -- 1: soft-deleted
);

-- A note's attachments, the file bytes stored in the database.
CREATE TABLE IF NOT EXISTS note_files (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id  INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  name     TEXT NOT NULL,                                 -- the uploaded file's name
  mime     TEXT,
  size     INTEGER NOT NULL,                              -- bytes
  data     BLOB NOT NULL,
  created  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS note_files_note ON note_files(note_id);
