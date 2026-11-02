"""Download the clips in hits_all.json (one row each via the dataset viewer), named by clip id so nothing is fetched
twice. Clips from the first run (clips/<row>.flac, ids in hits.json) are reused. Adds "id" and "file" to each hit.

    python fetch_all.py hits_all.json clips_all/
"""
import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch_audio import ROWS, get  # noqa: E402


def file_name(clip_id: str) -> str:
    return hashlib.sha1(clip_id.encode()).hexdigest()[:16] + ".flac"


def find_row(hit):
    starts = [hit["row"]] if hit.get("exact") else []
    starts += [hit["row"] - 50, hit["row"] - 150, hit["row"] + 50, hit["row"] - 300, hit["row"] + 150]
    for start in starts:
        length = 1 if start == hit["row"] and hit.get("exact") else 100
        meta = get(f"{ROWS}&offset={max(0, start)}&length={length}")
        if not meta:
            continue
        row = next((r["row"] for r in json.loads(meta)["rows"] if r["row"]["text"] == hit["text"]), None)
        if row:
            return row
    return None


def fetch(hit, out: Path, known: dict):
    if hit["text"] in known and (out / known[hit["text"]][1]).exists():
        hit["id"], hit["file"] = known[hit["text"]]
        return "cached"
    row = find_row(hit)
    if not row:
        return "not found"
    hit["id"], hit["file"] = row["id"], file_name(row["id"])
    if (out / hit["file"]).exists():
        return "cached"
    audio = get(row["audio"][0]["src"])
    if not audio:
        return "no audio"
    (out / hit["file"]).write_bytes(audio)
    return "ok"


def main():
    hits_path, out = Path(sys.argv[1]), Path(sys.argv[2])
    hits = json.loads(hits_path.read_text(encoding="utf-8"))
    out.mkdir(exist_ok=True)
    known = {}  # transcript -> (id, file name), from the first run and earlier passes
    old = HERE / "hits.json"
    if old.exists():
        for h in json.loads(old.read_text(encoding="utf-8")):
            src = HERE / "clips" / f"{h['row']}.flac"
            if h.get("id") and src.exists():
                dest = out / file_name(h["id"])
                if not dest.exists():
                    shutil.copy2(src, dest)
                known[h["text"]] = (h["id"], dest.name)
    for h in hits:
        if h.get("file"):
            known[h["text"]] = (h["id"], h["file"])
    t0, counts = time.time(), {}
    with ThreadPoolExecutor(6) as pool:
        for k, result in enumerate(pool.map(lambda h: fetch(h, out, known), hits)):
            counts[result] = counts.get(result, 0) + 1
            if k % 50 == 0:
                print(f"{k + 1}/{len(hits)} {counts} {time.time() - t0:.0f}s", flush=True)
    hits_path.write_text(json.dumps(hits), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.glob("*.flac"))
    print(f"done: {counts}, {len(list(out.glob('*.flac')))} clips, {size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
