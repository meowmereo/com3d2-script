Install (Required Blender 3.3-3.6 and [CM3D2 Converter](https://github.com/luvoid/Blender-CM3D2-Converter/releases))
overwrite anm_export.py in cm3d2 converter folder

All Frame
"Export All Frames" checkbox ON
- Use for maximum compatibility
- File size: Full size (no optimization) or use bone filter (maybe reduce 30-50%)

Optimized Export (Recommended)
- "Export All Frames" checkbox OFF  
- Reveals Keyframe Optimization options
- Use for file size reduction

#Optimization Methods#
# SIMPLE
How it works: Keep every Nth frame uniformly
Frame Step = 2: Keep frames 0, 2, 4, 6, 8...
Frame Step = 3: Keep frames 0, 3, 6, 9, 12...

# DENSITY
Settings:
- Dense Bone Threshold (0.1-1.0): When to consider bone "dense"
  - 0.8 = bone with >80% keyframes is "dense"
- Dense Bone Sampling (2-5): Reduction factor for dense bones
  - 2 = keep every 2nd keyframe from dense bones

# MOTION
Settings:
- Motion Threshold (0.001-0.1): Minimum change to keep keyframe
  - Lower = more sensitive, larger files
- Time Gap Limit (5-30): Max frames between keyframes
---

# RDP - Curve 
How it works: Ramer-Douglas-Peucker curve simplification
Settings:
- RDP Tolerance (0.001-0.1): Curve simplification tolerance
 - Lower = more accurate, larger files
- Min Frame Distance (1-10): Minimum frames between keyframes
 - Higher = fewer keyframes, smaller files
  
Recommend setting

-SIMPLE: Frame Step = 2
-DENSITY: Threshold = 0.6, Sampling = 2  
-MOTION: Threshold = 0.005, Gap = 5
-RDP: Tolerance = 0.005, Distance = 1
