# Findings

## Requirement Coverage Notes
- Requirements cover LAN auto-discovery, 3+ nodes, screen topology, fish animation, cross-screen transfer, water audio/effects, user controls, full/window display, network messages, and non-functional performance/reliability/usability/extensibility targets.
- Repository appears to be a Python/Pygame application with modules for app, network, topology, fish model, rendering, audio, controls, and config.
- README states this is a Python + Pygame MVP with UDP discovery, manual/auto topology calibration, fish transfer, status panel, water audio, and headless tests.
- Implementation evidence found so far:
  - `NetworkManager` implements DISCOVER, DISCOVER_RESPONSE, HEARTBEAT, STATUS_SYNC, NODE_JOIN, NODE_LEAVE, TOPOLOGY_UPDATE plus fish transfer/ack.
  - `TopologyCoordinator` auto-assigns neighbor directions deterministically by node_id order, with manual overrides.
  - `Fish` implements random movement, boids-like behavior, depth/size/color, turning, tail phase, transfer payloads, and expired-transfer recovery.
  - `AquariumRenderer` draws background, bubbles, ripples, fish, status/control console.
  - `AudioController` loops an MP3 background sound or synthesized fallback.

## Code / Project Issues
- `cyberfish/assets/fish3d/` is currently untracked in git status.
- Current checked-in `config.json` has `sound_enabled: false`, so the project does not start with water audio enabled unless the user toggles it.
- Automatic topology appears algorithmic rather than based on physical display coordinates; need verify whether this satisfies the requirement to determine actual left/right/up/down placement.
- Code search found no references to `fish3d`, PNG loading, or the sprite manifest; the untracked fish3d assets are not integrated into rendering.
- `scripts/topology_demo.py --nodes 3` converges to an inverse-consistent left/right cycle based on node IDs, not a physical screen layout.
- Cross-screen protocol uses lowercase `fish_transfer` / `transfer_ack` instead of the requirement table's uppercase `FISH_TRANSFER` naming.

## Verification Notes
- `venv/bin/python -m unittest discover -v` passed: 87 tests.
- `venv/bin/python scripts/topology_demo.py --nodes 3 --seconds 12 --port 37802 --broadcast 127.255.255.255 --quiet` exited 0 and reported inverse consistency / converged.
- Existing automated tests do not prove 3-real-host operation, <100ms cross-screen latency, >=25 FPS under load, or 30-minute stability.
