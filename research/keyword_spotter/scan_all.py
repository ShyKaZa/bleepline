"""Resumable scan of every People's Speech "clean" shard's transcripts (HTTP range requests, no audio downloaded).

Each shard's result is saved to shards/<name>.json as soon as it's read, so a crash or a throttling pause loses
nothing; rerunning continues. Row numbers are exact: each shard's row count comes from its own parquet footer.

    python scan_all.py hits_all.json [threads]
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scan_text import BASE, SWEAR, RangeFile, pq  # noqa: E402

CACHE = HERE / "shards"


def scan(name):
    out = CACHE / f"{name}.json"
    if out.exists():
        return "cached"
    for attempt in range(6):
        try:
            f = RangeFile(BASE + name)
            pf = pq.ParquetFile(f, pre_buffer=True)
            texts = pf.read(columns=["text"]).column("text").to_pylist()
            found = [(i, t) for i, t in enumerate(texts) if SWEAR.search(t.lower())]
            out.write_text(json.dumps({"rows": pf.metadata.num_rows, "found": found}), encoding="utf-8")
            return "ok"
        except Exception as exc:
            print(f"{name}: attempt {attempt + 1} failed ({str(exc)[:80]})", flush=True)
            time.sleep(30 * (attempt + 1))  # back off: the server throttles bursts
    return "failed"


def main():
    out, threads = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 10
    CACHE.mkdir(exist_ok=True)
    tree = json.load(urllib.request.urlopen(
        "https://huggingface.co/api/datasets/MLCommons/peoples_speech/tree/main/clean"))
    names = sorted(x["path"].split("/")[-1] for x in tree if x["path"].startswith("clean/train-"))
    names = names[:int(os.environ.get("SCAN_LIMIT", len(names)))]  # for a quick test run
    for rnd in range(3):  # later rounds retry shards that failed
        todo = [n for n in names if not (CACHE / f"{n}.json").exists()]
        if not todo:
            break
        t0, counts = time.time(), {}
        print(f"round {rnd + 1}: {len(todo)} shards to scan", flush=True)
        with ThreadPoolExecutor(threads) as pool:
            for k, result in enumerate(pool.map(scan, todo)):
                counts[result] = counts.get(result, 0) + 1
                if k % 20 == 0:
                    print(f"  {k + 1}/{len(todo)} {counts} {time.time() - t0:.0f}s", flush=True)
    hits, offset, exact = [], 0, True
    for name in names:
        path = CACHE / f"{name}.json"
        if not path.exists():
            exact = False  # rows after an unread shard are estimates; fetching looks around them
            offset += 1868
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        hits += [{"row": offset + i, "text": t, "words": SWEAR.findall(t.lower()), "shard": name, "exact": exact}
                 for i, t in data["found"]]
        offset += data["rows"]
    out.write_text(json.dumps(hits), encoding="utf-8")
    print(f"{len(hits)} clips with swear words in {offset} rows ({'exact' if exact else 'some estimated'} positions)")


if __name__ == "__main__":
    main()
