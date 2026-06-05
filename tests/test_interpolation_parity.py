"""Parity test: the C++ pacer.interpolate_timestamps must match the original
PyTorch (notebook) parametric optimizer on the same input.

Run directly (`python tests/test_interpolation_parity.py`) or via pytest. Needs
the `pacer` binding and torch (both in the pixi env).
"""

import numpy as np
import torch

import pacer


def torch_interpolate(floor, ceil, di, rough_freq):
    """Replicates the notebook's parametric (t2) optimization in float64."""
    floor_t = torch.tensor(floor, dtype=torch.float64)
    ceil_t = torch.tensor(ceil, dtype=torch.float64)
    di_t = torch.tensor(di, dtype=torch.float64)

    phase = torch.tensor(floor[0], dtype=torch.float64, requires_grad=True)
    frequency = torch.tensor(rough_freq, dtype=torch.float64, requires_grad=True)

    def make_t():
        return phase + 1.0 / frequency * (di_t.cumsum(0) - 1)

    def loss(x):
        my_diffs = (x[1:] - x[:-1]) / di_t[1:]
        spacing = ((my_diffs - my_diffs.mean()) ** 2).mean()
        constraints = (((floor_t - x).clip(min=0) + (x - ceil_t).clip(min=0)) ** 2).mean()
        return spacing + constraints

    for lr in [1e-1, 1e-2, 1e-3]:
        opt = torch.optim.Adam([phase, frequency], lr=lr)
        for _ in range(100):
            opt.zero_grad()
            loss(make_t()).backward()
            opt.step()

    return make_t().detach().numpy(), float(phase), float(frequency)


def _make_fixture():
    rng = np.random.default_rng(0)
    n = 80
    true_phase, true_freq = 3.0, 8.0
    di = np.where(rng.random(n) < 0.2, 2.0, 1.0)
    di[0] = 1.0
    c = np.cumsum(di) - 1.0
    t_true = true_phase + c / true_freq
    floor = (t_true - 0.06).tolist()
    ceil = (t_true + 0.06).tolist()
    # Deliberately-off but reasonable initial frequency, fed to both optimizers.
    init_freq = true_freq * 0.85
    return floor, ceil, di.tolist(), init_freq


def test_cpp_matches_torch():
    floor, ceil, di, rough_freq = _make_fixture()

    cpp = pacer.interpolate_timestamps(
        pacer.InterpolationInput(floor=floor, ceil=ceil, di=di), rough_freq
    )
    t_torch, p_torch, f_torch = torch_interpolate(floor, ceil, di, rough_freq)

    # Same loss, same Adam, same LR schedule + init => near-identical fit.
    # Thresholds are tight (close to the observed ~1e-15 agreement) so the test
    # actually guards the hand-derived analytic gradient against regressions.
    assert abs(cpp.phase - p_torch) < 1e-6, (cpp.phase, p_torch)
    assert abs(cpp.frequency - f_torch) < 1e-6, (cpp.frequency, f_torch)
    max_dt = float(np.max(np.abs(np.array(cpp.timestamps) - t_torch)))
    assert max_dt < 1e-9, max_dt
    print(
        f"parity OK: phase {cpp.phase:.5f} vs {p_torch:.5f}, "
        f"freq {cpp.frequency:.5f} vs {f_torch:.5f}, max |dt| {max_dt:.2e}"
    )


if __name__ == "__main__":
    test_cpp_matches_torch()
    print("all parity checks passed")
