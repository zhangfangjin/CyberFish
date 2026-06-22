# Task Plan: CyberFish MySQL Persistence

## Goal
Implement the approved MySQL 8.0 persistence design without putting database I/O on the Pygame/UDP real-time path.

## Phases

### Phase 1: Schema and integration boundaries
**Status:** complete

### Phase 2: Migration, cache fields, and storage layer
**Status:** complete

### Phase 3: UDP configuration/metrics and app integration
**Status:** complete

### Phase 4: Tests and documentation
**Status:** complete

### Phase 5: Complete verification and diff review
**Status:** complete

## Decisions
- Only the elected admin connects to MySQL; display nodes never receive DB credentials.
- MySQL is authoritative for managed settings; local JSON is the bootstrap and last-known-good cache.
- Database failures must not stop animation or UDP transfers; writes use an asynchronous bounded queue.
- Manual topology is restorable; automatic topology is recalculated and stored only as observed history.
- Fish coordinates and per-frame state are not stored.
- Preserve unrelated dirty-worktree changes.

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| System `python3` has no Pygame, so UI and smoke tests cannot import | 1 | Use the repository virtual environment documented by README |
| Existing state-sync unit test injects a fake network after `force_network_enabled=False` | 1 | Gate state sync on the managed data-plane setting; forced-off already prevents socket creation |
| MySQL official documentation lookup returned no page content | 2 | Keep to stable Connector/Python APIs and treat live MySQL execution as an explicit environment-level verification item |
| Temporary MySQL 9.6 initialization exited 2 without terminal output | 1 | Inspect the isolated `/tmp` initialization log before choosing a different invocation |
| `SOURCE` inside a one-line `mysql --execute` was parsed as server SQL (1064) | 1 | Create the database separately, then feed the migration through standard input |
| Project virtualenv did not yet contain the newly declared `mysql.connector` package | 1 | Install dependencies from the updated requirement before repository integration verification |
| Planning skill completion checker required structured phase headings and status records | 3 | Read the checker, use its supported format, and avoid quoting its raw search token in this log |
