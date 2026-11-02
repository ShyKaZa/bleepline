"""Download random People's Speech clips WITHOUT swearing: everyday talk the spotter must leave alone.

Rows are drawn from the scanned shards (shards/*.json, from scan_all.py) away from every swearing row; each clip's
transcript is checked again before keeping it.

    python fetch_negatives.py negatives.json negatives/ [count]
"""
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch_all import file_name  # noqa: E402
from fetch_audio import ROWS, get  # noqa: E402
from scan_text import SWEAR  # noqa: E402


def swearing_rows():
    """Every row whose transcript swears, and the total row count, from the per-shard scan cache."""
    rows, offset = set(), 0
    for path in sorted((HERE / "shards").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.update(offset + i for i, _ in data["found"])
        offset += data["rows"]
    return rows, offset


def fetch(row, out: Path):
    meta = get(f"{ROWS}&offset={row}&length=1")
    if not meta:
        return None, "no row"
    r = json.loads(meta)["rows"][0]["row"]
    if SWEAR.search(r["text"].lower()):
        return None, "swears"
    dest = out / file_name(r["id"])
    if not dest.exists():
        audio = get(r["audio"][0]["src"])
        if not audio:
            return None, "no audio"
        dest.write_bytes(audio)
    return {"row": row, "id": r["id"], "file": dest.name, "text": r["text"]}, "ok"


def main():
    out_json, out = Path(sys.argv[1]), Path(sys.argv[2])
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
    out.mkdir(exist_ok=True)
    bad, total = swearing_rows()
    rng = random.Random(11)
    rows = []
    while len(rows) < count:
        row = rng.randrange(total)
        if not any(row + d in bad for d in range(-2, 3)) and row not in rows:
            rows.append(row)
    t0, counts, kept = time.time(), {}, []
    with ThreadPoolExecutor(6) as pool:
        for k, (clip, result) in enumerate(pool.map(lambda r: fetch(r, out), rows)):
            counts[result] = counts.get(result, 0) + 1
            if clip:
                kept.append(clip)
            if k % 50 == 0:
                print(f"{k + 1}/{len(rows)} {counts} {time.time() - t0:.0f}s", flush=True)
    out_json.write_text(json.dumps(kept), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.glob("*.flac"))
    print(f"done: {counts}, {len(kept)} clips, {size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
