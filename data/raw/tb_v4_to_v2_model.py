"""Map Terminal-Bench v4 scores onto the v2 scale.

Data: Artificial Analysis paired runs (same harness) of Terminal-Bench 2.1
and 4.0 for the same model variants. v2.1 is the verified refresh of v2.0.
Source: https://artificialanalysis.ai/evaluations/terminalbench-2-1
        https://artificialanalysis.ai/evaluations/terminalbench-4-0
"""
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

df = pd.read_csv("/work/paired_v2_v4.csv")
x = df["tb40_pct"].values   # v4 score (predictor)
y = df["tb21_pct"].values   # v2.1 score (target scale)

# --- models ---
lr = stats.linregress(x, y)
a, b = lr.slope, lr.intercept
c2, c1, c0 = np.polyfit(x, y, 2)

def qmap(v4_new):
    p = stats.rankdata(np.append(x, v4_new))[-1] / (len(x) + 1)
    return np.quantile(y, p)

rmse = np.sqrt(np.mean((y - (a * x + b)) ** 2))
print(f"LINEAR  v2 = {a:.4f}*v4 + {b:.4f}   R2={lr.rvalue**2:.3f}  RMSE={rmse:.2f}pp")
print(f"QUAD    v2 = {c2:.5f}*v4^2 + {c1:.4f}*v4 + {c0:.4f}")

json.dump({"slope": a, "intercept": b, "r2": lr.rvalue**2, "rmse": rmse,
           "quad": [c2, c1, c0], "n": len(x)},
          open("/work/tb_v4_to_v2_model.json", "w"), indent=1)

# --- figure ---
fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

ax = axes[0]
ax.scatter(x, y, s=55, zorder=3, color="#1f77b4", edgecolor="white", linewidth=0.6)
xs = np.linspace(0, x.max() * 1.05, 200)
ax.plot(xs, a * xs + b, "--", color="#d62728", lw=1.8,
        label=f"linear: v2 = {a:.2f}·v4 + {b:.1f}  (R²={lr.rvalue**2:.2f})")
ax.plot(xs, np.polyval([c2, c1, c0], xs), "-", color="#2ca02c", lw=1.8,
        label=f"quadratic  (R²={1 - np.sum((y - np.polyval([c2, c1, c0], x))**2) / np.sum((y - y.mean())**2):.2f})")
ax.plot([0, 100], [0, 100], ":", color="grey", lw=1.2, label="identity (no change)")
# annotate a few points
for _, r in df.iterrows():
    short = r["name"].split(" (")[0]
    if r["tb40_pct"] > 30 or r["tb21_pct"] < 60:
        ax.annotate(short, (r["tb40_pct"], r["tb21_pct"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=7.5)
ax.set_xlabel("Terminal-Bench 4.0 score (%)")
ax.set_ylabel("Terminal-Bench 2.1 score (%)")
ax.set_title("Paired model runs: v4 → v2 scale\n(Artificial Analysis, same harness, n=20)")
ax.legend(fontsize=8, loc="lower right")
ax.set_xlim(-2, 65); ax.set_ylim(45, 95)
ax.grid(alpha=0.25)

ax = axes[1]
grid = np.linspace(0, 60, 200)
ax.plot(grid, a * grid + b, "--", color="#d62728", lw=1.8, label="linear")
ax.plot(grid, np.polyval([c2, c1, c0], grid), "-", color="#2ca02c", lw=1.8, label="quadratic")
ax.plot(grid, [qmap(v) for v in grid], "-.", color="#9467bd", lw=1.8, label="quantile map")
ax.plot([0, 60], [0, 60], ":", color="grey", lw=1.2, label="identity")
# observed range shading
ax.axvspan(x.min(), x.max(), color="grey", alpha=0.08)
ax.text(1, 88, "shaded = observed\nv4 range", fontsize=7.5, color="dimgrey")
ax.set_xlabel("Terminal-Bench 4.0 score (%)")
ax.set_ylabel("mapped v2.1-equivalent (%)")
ax.set_title("Mapping functions (trust inside shaded range only)")
ax.legend(fontsize=8)
ax.grid(alpha=0.25)

fig.tight_layout()
fig.savefig("/work/tb_v4_to_v2.png", dpi=150)
print("saved /work/tb_v4_to_v2.png")
