- `ai import-names` copies the git-tree registry (pool, manual names, held rows, renames and policy
  snapshot) into the fleet's Dolt database in one commit and prints its counts; a second run finds
  nothing to import. `ai seed-name-verdicts -f FILE` applies the owner's approvals at the scope each
  was given, after checking every part of the seed file against an owner-approved oracle pinned at
  a fleet-repository commit by a separate cutover record. `ai list-migrations` lists every rename,
  and `ai find-names` searches names by status, verdict scope, holder and hold state in one query,
  or with `-H/--history NAME` shows every past value (Dolt only).
- On the database registries every session name has a general status (`unreviewed`, `rejected` or
  `approved`), and names can be approved or rejected for one repository, host or session only.
  The picker, spare top-up and offline spares use only approved names that no verdict rejects
  for the launch, and refuse when the status data is missing or unreadable. A name chosen by hand
  needs no approval, but a rejected one needs a session approval. `ai set-name-status` and
  `ai set-name-verdict` record who decided (`-a/--actor`) apart from the session that wrote it;
  `ai list-names` shows each name's status and `-p/--pool` lists the whole pool.
- The session-name registry can live in a Dolt database on its own ref of the fleet repository
  (`registry_backend = "dolt"` with `schema = 2` in `config/session-names.toml`), reached through
  the `dolt` program. Every write is guarded in the statement itself, pushed without force and read
  back from the remote tip before it counts. An install with no fleet repository now keeps its
  registry in a local SQLite file with the same tables and constraints, seeded with the shipped
  name list.
