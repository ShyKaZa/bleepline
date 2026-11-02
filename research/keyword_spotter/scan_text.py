"""Read only the transcript column of People's Speech parquet shards via HTTP range requests; list profane rows."""
import io, json, re, sys, time, urllib.request
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pylib"))
import pyarrow.parquet as pq

ROWS_PER_SHARD = 1868  # every shard but the last (1,501,271 rows in 804 shards)
BASE = "https://huggingface.co/datasets/MLCommons/peoples_speech/resolve/main/clean/"
SWEAR = re.compile(r"\b(fuck\w*|motherfuck\w*|shit\w*|bullshit\w*|bitch\w*|asshole\w*|bastard\w*|dick|cunt\w*|crap|piss\w*|damn\w*)\b")

class RangeFile(io.RawIOBase):
    def __init__(self, url):
        self.url, self.pos, self.fetched = url, 0, 0
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos
    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0: return 0
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as r: data = r.read()
                break
            except Exception:
                time.sleep(5)
        b[:len(data)] = data; self.pos += len(data); self.fetched += len(data)
        return len(data)

def scan(name):
    for attempt in range(3):
        try:
            f = RangeFile(BASE + name)
            pf = pq.ParquetFile(f, pre_buffer=True)  # merges nearby reads into fewer requests
            texts = pf.read(columns=["text"]).column("text").to_pylist()
            found = [(i, None, text) for i, text in enumerate(texts) if SWEAR.search(text.lower())]
            return pf.metadata.num_rows, found, f.fetched
        except Exception as exc:
            err = exc
            time.sleep(10)
    print(f"{name}: FAILED {err}", flush=True)
    return None


def main():
    from concurrent.futures import ThreadPoolExecutor
    shards, out = int(sys.argv[1]), Path(sys.argv[2])
    tree = json.load(urllib.request.urlopen("https://huggingface.co/api/datasets/MLCommons/peoples_speech/tree/main/clean"))
    every = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    names = sorted(x["path"].split("/")[-1] for x in tree if x["path"].startswith("clean/train-"))[:shards]
    rows_per = {}
    wanted = set(names[::every])
    t0, results = time.time(), {}
    todo = [n for n in names if n in wanted]
    with ThreadPoolExecutor(24) as pool:
        for k, (name, res) in enumerate(zip(todo, pool.map(scan, todo))):
            results[name] = res
            if k % 25 == 0:
                done = [r for r in results.values() if r]
                print(f"{k + 1}/{len(todo)} shards, {sum(len(r[1]) for r in done)} hits, "
                      f"{sum(r[2] for r in done) / 1e6:.0f} MB, {time.time() - t0:.0f}s", flush=True)
    hits, odd = [], []
    for j, name in enumerate(names):
        res = results.get(name)
        if res is None:
            continue
        rows, found, _ = res
        if rows != ROWS_PER_SHARD and j != len(names) - 1:
            odd.append(name)  # positions would be off: leave this shard's hits out
            continue
        hits += [{"row": j * ROWS_PER_SHARD + i, "id": id_, "text": text, "words": SWEAR.findall(text.lower()),
                  "shard": name} for i, id_, text in found]
    offset, complete = sum(r[0] for r in results.values() if r), not odd
    out.write_text(json.dumps(hits), encoding="utf-8")
    print(f"{len(hits)} clips with swear words in {offset} rows{'' if complete else ' (stopped at a failed shard)'}")


if __name__ == "__main__":
    main()
