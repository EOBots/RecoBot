# RecoTrainer

Lightweight, GUI-only trainer for Endless Online–style clients. RecoTrainer focuses on: setting your home location, reading a walkable grid, targeting specific NPC IDs, burst-healing on EXP gain, tap-attacking during combat, and wandering/patrolling intelligently within a radius. All logs and status updates stay inside the GUI (no console window).

## Features

- Set Home: read current X/Y from memory and save as your patrol center.
- Walkable Grid: load a JSON of passable tiles; A* pathfinding between points.
- Target NPC IDs: attack only when the current target NPC ID matches your list.
- Burst Heal on EXP: after each EXP gain, burst-tap the heal key.
- Combat Loop: taps the configured attack key at a fixed rate.
- Wander/Patrol: move intelligently around Home using walkable tiles.
- GUI-only status & logs; no console output.
- Editable memory addresses in GUI.

## Requirements

- Windows recommended
- Python 3.10+
- Dependencies:
  - Frida 16.x
  - frida-tools 16.x
  - pyautogui
  - tkinter

## Installation

```bash
pip install "frida==16.*" "frida-tools==16.*" pyautogui
```

## Quick Start

1. Place `RecoTrainer.py` and `walkable.json` together.
2. Run `python RecoTrainer.py`.
3. Attach to the process (e.g., endor.exe).
4. Set memory addresses in the GUI.
5. Set Home = Current.
6. Add target NPC IDs.
7. Press Start.

## Walkable JSON Format

```json
{
  "walkable": [
    [50, 50],
    [51, 50]
  ]
}
```

## Build EXE

```bash
pyinstaller --noconsole --onefile --name RecoTrainer RecoTrainer.py
```

## License

MIT
