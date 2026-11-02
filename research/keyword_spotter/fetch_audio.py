"""Download only the People's Speech clips listed in hits.json, via the Hugging Face dataset viewer (one row each).

    python fetch_audio.py hits.json clips/
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROWS = "https://datasets-server.huggingface.co/rows?dataset=MLCommons/peoples_speech&config=clean&split=train"


def get(url, tries=6):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Bleepline-research"}),
                                        timeout=90) as r:
                return r.read()
        except Exception:
            time.sleep(5 * (k + 1))
    return None


def fetch(hit, out: Path):
    dest = out / f"{hit['row']}.flac"
    if dest.exists():
        return "cached"
    row = None
    for start in (hit["row"] - 50, hit["row"] - 150, hit["row"] + 50, hit["row"] - 250, hit["row"] + 150):
        meta = get(f"{ROWS}&offset={max(0, start)}&length=100")  # positions can drift: look around them
        if not meta:
            continue
        row = next((r["row"] for r in json.loads(meta)["rows"] if r["row"]["text"] == hit["text"]), None)
        if row:
            break
    if not row:
        return "not found"
    hit["id"] = row["id"]
    audio = get(row["audio"][0]["src"])
    if not audio:
        return "no audio"
    dest.write_bytes(audio)
    return "ok"


def main():
    hits, out = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), Path(sys.argv[2])
    out.mkdir(exist_ok=True)
    t0, counts = time.time(), {}
    with ThreadPoolExecutor(6) as pool:
        for k, result in enumerate(pool.map(lambda h: fetch(h, out), hits)):
            counts[result] = counts.get(result, 0) + 1
            if k % 50 == 0:
                print(f"{k + 1}/{len(hits)} {counts} {time.time() - t0:.0f}s", flush=True)
    Path(sys.argv[1]).write_text(json.dumps(hits), encoding="utf-8")  # now with clip ids
    size = sum(p.stat().st_size for p in out.glob("*.flac"))
    print(f"done: {counts}, {size / 1e6:.0f} MB in {out}")


if __name__ == "__main__":
    main()
