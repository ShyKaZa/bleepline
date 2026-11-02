"""A stand-in for a game that keeps the GPU at 100%: back-to-back half-precision matrix products. Prints how many
it manages per second every 10 s, so what live recognition costs it shows up as a lower rate. Dev only.

    python burn_gpu.py <seconds>
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "torchlib_cuda"))
import torch  # noqa: E402

a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
end, t0, n = time.time() + float(sys.argv[1]), time.time(), 0
while time.time() < end:
    for _ in range(20):
        a @ a
    torch.cuda.synchronize()
    n += 20
    if time.time() - t0 >= 10:
        print(f"{time.strftime('%H:%M:%S')} {n / (time.time() - t0):.1f} products/s", flush=True)
        t0, n = time.time(), 0
