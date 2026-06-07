"""Compare heading sources to quantify the IMU map upside (limitation #3, cross-track jitter).

Sources:
  GPS-pos heading: atan2 of point-to-point lat/lon deltas (what we have now, before boxcar).
  GYRO yaw:        integrate the vertical-axis gyro rate (GoPro ACCL/GYRO order is Z,X,Y; Z is
                   the camera's optical axis... for a kart-mounted cam yaw is about vertical).
  CORI yaw:        the camera-orientation quaternion's yaw (already Kalman-fused on-camera at 60Hz).

We don't know the exact mount axis, so we report the NOISE (HF second-difference std) of each
heading source's RATE -- the relevant quantity for jitter, independent of absolute alignment.
A much lower HF noise for GYRO/CORI means IMU heading would visibly de-jitter the map trace."""
import numpy as np

def load(fn):
    return np.loadtxt(fn, delimiter=",")

gps = load("/tmp/claude/gps9.csv")   # t,lat,lon,alt,sp2d,sp3d,days,secs,dop,fix
gyro = load("/tmp/claude/gyro.csv")  # t,gz,gx,gy (rad/s)
cori = load("/tmp/claude/cori.csv")  # t,w,x,y,z
accl = load("/tmp/claude/accl.csv")  # t,az,ax,ay (m/s^2)

# Restrict to a moving window (speed > 5 m/s) so heading is well-defined.
mov = gps[:, 5] > 5.0
g = gps[mov]
print(f"GPS samples moving: {len(g)} / {len(gps)}  speed range {g[:,5].min():.1f}-{g[:,5].max():.1f} m/s")

# --- GPS position-differenced heading ---
lat0 = np.median(g[:, 1])
mlat = 111320.0; mlon = 111320.0 * np.cos(np.radians(lat0))
x = (g[:, 2] - np.median(g[:, 2])) * mlon
y = (g[:, 1] - lat0) * mlat
hdg_gps = np.unwrap(np.arctan2(np.diff(y), np.diff(x)))
# HF noise = std of 2nd difference (removes the real turning signal, leaves jitter)
gps_hf = np.std(np.diff(hdg_gps, 2))
print(f"GPS-pos heading HF noise (2nd-diff std): {np.degrees(gps_hf):.3f} deg/sample")

# --- GYRO yaw rate: try each axis, pick the one whose integral best matches GPS heading turn ---
# Integrate each gyro axis over the same window; compare turn-rate to GPS heading rate.
def gyro_rate_noise(axis_col):
    t = gyro[:, 0]; w = gyro[:, axis_col]
    # HF noise of the rate itself (1st diff std), scaled to per-GPS-sample (0.1s) equivalent
    return np.std(np.diff(w))
for c, name in [(1, "gz"), (2, "gx"), (3, "gy")]:
    print(f"GYRO {name} rate HF noise (1st-diff std): {gyro_rate_noise(c):.5f} rad/s  "
          f"(range {gyro[:,c].min():.2f}..{gyro[:,c].max():.2f})")

# --- CORI quaternion yaw ---
t = cori[:, 0]; w, qx, qy, qz = cori[:,1], cori[:,2], cori[:,3], cori[:,4]
# yaw from quaternion (z-axis rotation)
yaw = np.unwrap(np.arctan2(2*(w*qz + qx*qy), 1 - 2*(qy*qy + qz*qz)))
cori_hf = np.std(np.diff(yaw, 2))
print(f"CORI yaw HF noise (2nd-diff std): {np.degrees(cori_hf):.4f} deg/sample @60Hz")

# To compare CORI to GPS fairly, resample CORI yaw onto GPS times and take its rate noise.
g_all = gps  # all GPS times in window
yaw_at_gps = np.interp(g[:,0], t, yaw)
cori_hf_atgps = np.std(np.diff(yaw_at_gps, 2))
print(f"CORI yaw HF noise resampled @GPS 10Hz (2nd-diff std): {np.degrees(cori_hf_atgps):.4f} deg/sample")
print()
print(f"RATIO GPS/CORI heading-noise @10Hz: {gps_hf/cori_hf_atgps:.1f}x  "
      f"(CORI yaw is this many times cleaner than GPS-differenced heading)")
