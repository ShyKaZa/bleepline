"""real.npz -> real_train.npz (training recordings only) and real_test.npz (held-out recordings, for evaluation only)."""
import sys

import numpy as np

d = np.load(sys.argv[1])
test = d["val"]
np.savez_compressed(sys.argv[2], X=d["X"][~test], y=d["y"][~test], utt=d["utt"][~test],
                    val=np.zeros((~test).sum(), bool))
np.savez_compressed(sys.argv[3], X=d["X"][test], y=d["y"][test], utt=d["utt"][test], val=np.ones(test.sum(), bool))
print(f"train {(~test).sum()} windows ({(d['y'][~test] == 1).sum()} positive), "
      f"test {test.sum()} windows ({(d['y'][test] == 1).sum()} positive)")
