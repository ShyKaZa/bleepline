"""How much swearing is in YODAS2 (espnet/yodas2: CC-BY 3.0 YouTube videos), from its transcripts only.

Audio comes in ~1.4 GB archives (one per transcript file, ~80 videos each) with no per-clip download, so this
finds the archives with the most swearing before anything big is fetched. Each transcript file's result is saved
to <out>/<folder>_<file>.json, so reruns continue.

    python scan_yodas.py <out dir> [files per folder]
"""
import json
import random
import sys
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch_audio import get  # noqa: E402
from scan_text import SWEAR  # noqa: E402

API = "https://huggingface.co/api/datasets/espnet/yodas2/tree/main/data"
FILES = "https://huggingface.co/datasets/espnet/yodas2/resolve/main/data"


def listing(path=""):
    return json.load(urllib.request.urlopen(f"{API}/{path}".rstrip("/")))


def scan(folder, name, out: Path):
    dest = out / f"{folder}_{name}"
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    raw = get(f"{FILES}/{folder}/text/{name}")
    if not raw:
        return None
    n, found = 0, []
    for video in json.loads(raw):
        for key, text in video["text"].items():
            n += 1
            if SWEAR.search(text.lower()) or "[ __ ]" in text:
                found.append((key, text))
    result = {"folder": folder, "file": name, "utterances": n, "found": found}
    dest.write_text(json.dumps(result), encoding="utf-8")
    return result


def main():
    out = Path(sys.argv[1])
    per_folder = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(5)
    folders = [x["path"].split("/")[-1] for x in listing() if x["path"].split("/")[-1].startswith("en")]
    jobs = []
    for folder in folders:
        names = sorted(x["path"].split("/")[-1] for x in listing(f"{folder}/text") if x["type"] == "file")
        jobs += [(folder, name) for name in rng.sample(names, min(per_folder, len(names)))]
    print(f"{len(jobs)} transcript files from {len(folders)} English folders", flush=True)
    with ThreadPoolExecutor(8) as pool:
        results = [r for r in pool.map(lambda j: scan(*j, out), jobs) if r]
    n = sum(r["utterances"] for r in results)
    hits = sum(len(r["found"]) for r in results)
    words = Counter(w for r in results for _, t in r["found"] for w in SWEAR.findall(t.lower()))
    words["[ __ ] (masked)"] = sum(t.count("[ __ ]") for r in results for _, t in r["found"])
    print(f"{len(results)} files: {n} utterances, {hits} with swearing (1 in {n // max(hits, 1)})")
    print("words:", words.most_common(20))
    counts = sorted((len(r["found"]) for r in results), reverse=True)
    for top in (10, 50, 100):
        print(f"best {top} archives: {sum(counts[:top])} swearing utterances ({sum(counts[:top]) / top:.0f} each)")


if __name__ == "__main__":
    main()
