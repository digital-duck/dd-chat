# Deployment plan: login and a hosted PostgreSQL database

Status: draft, 2026-11-14. The open questions at the end are for the owner to answer before work starts.

## Goal

Move dd-chat from a single-user local app (SQLite in `~/.dd-chat`) to a hosted multi-user app: users sign in, and their chats, settings and presets live in a hosted PostgreSQL database.

## Login

- **Who signs in**: only the owner, a small group, or the public? This sets how much has to be built (see open questions).
- **Method**: Streamlit's built-in OIDC (`st.login`, with Google or GitHub) is the least code. Email and password would mean storing and resetting passwords; avoid it.
- **Ownership**: every chat session, setting, preset and model selection gets a `user_id`, and every query filters on it. This is the part that must not leak data between users, so it needs tests that sign in as two users.

## Hosted PostgreSQL

- **Provider**: Neon, Supabase, RDS or another. Neon and Supabase have free tiers.
- **Database layer**: `chat_db.py` uses `sqlite3` directly. Move to SQLAlchemy or `psycopg` with a small connection pool: Streamlit reruns and the worker threads must not open a new connection per call.
- **Schema and migrations**: keep the `COLUMNS` list and the additive `ALTER TABLE` approach. Use PostgreSQL types (`jsonb`, `timestamptz`) and keep the schema check that runs once per process.
- **Concurrency**: the optimistic `rev` check in `save_session` becomes one conditional `UPDATE ... WHERE rev = ?`.
- **Existing data**: a one-off script imports the current `~/.dd-chat/multi_chat.db` into the owner's new user account.

## API keys and secrets

- Today a key typed on the Settings page goes into the process-wide `os.environ` and is kept in memory only (`src/env_keys.py`). With several users on one hosted process, that is a leak: one user's key would serve everyone's calls.
- Choose between: keys scoped per session (adapters would need the key passed per call, not read from `os.environ`), or each user's keys stored encrypted in the database. This is a bigger decision than the database move.
- The connection string belongs in `.env` or the environment as `DATABASE_URL`, which fits the existing precedence (typed > environment > `.env`).

## Packaging

- `pip install dd-chat` should keep working with no database setup: SQLite stays the default, and PostgreSQL is used only when `DATABASE_URL` is set.
- This also keeps the test suite (50 tests, no model or network calls) running offline against SQLite; add a PostgreSQL run in CI or behind a marker.

## Open questions

1. How many users: just the owner, a small group, or the public?
2. Which PostgreSQL host?
3. Do API keys stay per session, or are they stored per user (encrypted)?
