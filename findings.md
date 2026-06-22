# Findings

## Requirement Coverage Notes
- Requirements cover LAN auto-discovery, 3+ nodes, screen topology, fish animation, cross-screen transfer, water audio/effects, user controls, full/window display, network messages, and non-functional performance/reliability/usability/extensibility targets.
- Repository appears to be a Python/Pygame application with modules for app, network, topology, fish model, rendering, audio, controls, and config.

## Code / Project Issues
- `cyberfish/assets/fish3d/` is currently untracked in git status.

## Verification Notes
- Verification pending.

## MySQL Implementation Findings (2026-06-22)
- The application currently has no database dependency; `requirements.txt` contains only Pygame.
- `config.json` is the only durable store and deliberately clears topology/admin runtime fields on save.
- `NetworkManager` already exposes cumulative counters suitable for low-frequency metric reports.
- `STATUS_SYNC` runs at 10Hz and must not be reused for database writes; a separate 10-second metrics message is appropriate.
- The current `network_enabled=False` path closes the UDP socket, so remotely re-enabling it is impossible. Managed configuration requires keeping the management channel alive while disabling only cross-screen data flow.
- Existing admin command IDs and ACK handling can be retained for transient actions; durable settings need versioned configuration messages.
- Database support is opt-in through environment variables; no connector import occurs on display nodes or default runs.
- Configuration changes use optimistic version checks and are applied only after the database transaction succeeds.
- Per-minute aggregation handles duplicate/out-of-order sequences, process boot changes, and cumulative counter resets.
- Broadcast command audit stores an expected result count so partial ACK sets are not reported as complete.
