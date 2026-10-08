# Written here, not in the original falcon_ros2: the scene vocabulary CHAI3D renders.
# One virtual object in a haptic scene. `type` selects which fields are used.
# Closed solids (sphere/box/cylinder/ellipsoid/cone/mesh) are rendered by
# CHAI3D's finger-proxy (god-object, no pop-through); the half-space/field types
# (plane/spring/viscous/constant) are simple analytic forces.
string type            # sphere | box | cylinder | ellipsoid | cone | mesh
                       # | plane | spring | viscous | constant
float64[3] point       # centre / anchor / a point on the plane (m)
float64[3] normal      # plane outward normal, or cylinder/cone axis (need not be unit)
float64[3] size        # box half-extents, OR ellipsoid radii [rx,ry,rz] (m)
float64 radius         # sphere / cylinder / cone-base radius (m)
float64 height         # cylinder / cone height (m)
float64 stiffness      # N/m (any solid, and spring)
float64 damping        # N.s/m (viscous)
float64[3] force       # constant force vector (N)

# --- surface texture (how a solid feels) ---
float64 friction       # 0..1 grip/drag when sliding along the surface
float64 vibration_freq # Hz, buzz felt while touching the object (0 = none)
float64 vibration_amp  # N, amplitude of that buzz

# --- arbitrary AI-authored geometry (type == "mesh") ---
float64[] vertices     # flat [x0,y0,z0, x1,y1,z1, ...] in metres, relative to point
int32[]   triangles    # flat [i0,i1,i2, ...] indices into vertices
