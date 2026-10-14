from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

src = Path('/Users/joseph/Downloads')
out = Path(__file__).resolve().parent / 'run008_speed_comparison'
out.mkdir(exist_ok=True)
stem = 'VN300_2026-09-29_RUN008_'
a = pd.read_csv(src / (stem + 'STEERING.csv'))
b = pd.read_csv(src / (stem + 'BINARY.csv'))
t = 'Pi_Logger_Elapsed_Time_s'
for d in (a, b):
    d['bin'] = np.floor(d[t] * 10).astype(int)
x = b.groupby('bin')[['Speed_mph', 'VelUncertainty_mps']].mean().join(
    a.groupby('bin')[['Airspeed_mph', 'Airspeed_Voltage_V']].mean(), how='inner').dropna()
x['time_s'] = (x.index + .5) / 10
x['difference_mph'] = x.Airspeed_mph - x.Speed_mph
def stats(d):
    return dict(bins=len(d), vehicle_mean=float(d.Speed_mph.mean()), air_mean=float(d.Airspeed_mph.mean()),
                mean_difference=float(d.difference_mph.mean()), mae=float(d.difference_mph.abs().mean()),
                rmse=float(np.sqrt((d.difference_mph**2).mean())), correlation=float(d.Speed_mph.corr(d.Airspeed_mph)),
                ratio_of_means=float(d.Airspeed_mph.mean()/d.Speed_mph.mean()))
moving = x[x.Speed_mph >= 5]
summary = {'all': stats(x), 'vehicle_at_least_5_mph': stats(moving),
           'moving_uncertainty_below_1_mps': stats(moving[moving.VelUncertainty_mps < 1]),
           'moving_start_s': float(moving.time_s.min()), 'moving_end_s': float(moving.time_s.max()),
           'air_peak_mph': float(a.Airspeed_mph.max()), 'vehicle_peak_mph': float(b.Speed_mph.max()),
           'below_nominal_zero_rows': int((a.Airspeed_Range == 'below_nominal_zero').sum()),
           'air_rows': len(a)}
print(json.dumps(summary, indent=2))
print('Uncertainty >=1 m/s:', x.loc[x.VelUncertainty_mps >= 1, 'time_s'].agg(['min','max']).to_dict())
print('Largest binned air/vehicle difference:', x.loc[x.difference_mph.idxmin()].to_dict())
(out / 'summary.json').write_text(json.dumps(summary, indent=2))
plt.rcParams.update({'font.size': 11, 'axes.spines.top': False, 'axes.spines.right': False})
fig, ax = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [1, 1.6, 1]}, layout='constrained')
fig.suptitle('RUN008 · Airspeed versus VN300 vehicle speed\n29 September 2026', fontsize=17, fontweight='bold')
for axis in ax[:2]:
    axis.plot(x.time_s, x.Speed_mph, color='#1767a6', lw=1.8, label='Vehicle speed (VN300)')
    axis.plot(x.time_s, x.Airspeed_mph, color='#dc7130', lw=1.3, label='Airspeed sensor')
    axis.set_ylabel('Speed (mph)')
    axis.grid(alpha=.18)
ax[0].set_title('Full recording', loc='left')
ax[0].set_xlim(0, 231)
ax[0].legend(loc='upper right')
ax[1].set_title('Acceleration and braking detail', loc='left')
ax[1].set_xlim(43, 59)
for _, row in x[x.VelUncertainty_mps >= 1].iterrows():
    ax[1].axvspan(row.time_s-.05, row.time_s+.05, color='#858585', alpha=.16, lw=0)
ax[1].text(.02,.94,'Gray: reported VN300 velocity uncertainty ≥1 m/s', transform=ax[1].transAxes, va='top', fontsize=10)
ax[2].plot(x.time_s, x.difference_mph, color='#8b416f', lw=1.3)
ax[2].axhline(0, color='black', lw=.8)
ax[2].set_xlim(43, 59)
ax[2].set_title('Airspeed minus vehicle speed', loc='left')
ax[2].set_ylabel('Difference (mph)')
ax[2].grid(alpha=.18)
for axis in ax:
    axis.set_xlabel('Logger elapsed time (s)')
fig.get_layout_engine().set(rect=(0,.045,1,1))
fig.text(.06,.012,'Matched 0.1-second means on shared logger time; no time shift applied.\nSources: RUN008_STEERING.csv (Airspeed_mph) and RUN008_BINARY.csv (Speed_mph).', fontsize=9, color='#444444')
fig.savefig(out / 'airspeed_vs_vehicle_speed.png', dpi=170)
