// Written here, not in the original: CHAI3D renders the scene the model author
// so the hand can feel it. The node sums its force with impedance guidance and
// clamps the total.
#include "haptic_renderer.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>

#include "chai3d.h"

using namespace chai3d;

namespace falcon_ros2
{

static inline double clampd(double v, double lo, double hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

HapticRenderer::HapticRenderer(double tool_radius, double force_clamp_n,
                               double max_stiffness, double contact_damping)
    : tool_radius_(tool_radius),
      force_clamp_(force_clamp_n),
      max_stiffness_(max_stiffness),
      contact_damping_(contact_damping)
{
    world_ = new cWorld();
    proxy_ = new cAlgorithmFingerProxy();
    proxy_->initialize(world_, cVector3d(0.0, 0.0, 0.0));
    proxy_->setProxyRadius(tool_radius_);
}

HapticRenderer::~HapticRenderer()
{
    delete proxy_;
    delete world_;   // deletes its child meshes
}

void HapticRenderer::requestScene(const std::vector<HapticObjectSpec>& objects)
{
    std::lock_guard<std::mutex> lock(mutex_);
    pending_ = objects;
    dirty_ = true;
}

int HapticRenderer::objectCount()
{
    std::lock_guard<std::mutex> lock(mutex_);
    return object_count_;
}

void HapticRenderer::rebuildIfNeeded(const double pos[3])
{
    std::vector<HapticObjectSpec> desired;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!dirty_) return;
        desired.swap(pending_);
        dirty_ = false;
    }

    // Rebuild the CHAI3D world from scratch (infrequent — only on scene change).
    world_->deleteAllChildren();
    analytic_.clear();
    vib_freq_ = 0.0;
    vib_amp_ = 0.0;

    for (const auto& o : desired)
    {
        cMesh* mesh = build_solid(o);
        if (mesh != nullptr)
        {
            world_->addChild(mesh);
            // Track the strongest surface vibration in the scene.
            if (o.vib_amp > vib_amp_) { vib_amp_ = o.vib_amp; vib_freq_ = o.vib_freq; }
        }
        else  // plane / spring / viscous / constant -> analytic
        {
            analytic_.push_back(o);
        }
    }

    world_->computeGlobalPositions(true);
    // Start the proxy at the current hand position (free space, since a scene is
    // normally set while the hand is outside the objects). As the hand then
    // moves into an object, the proxy sweeps and detects the surface.
    // Limitation: if an object is spawned *around* an already-stationary hand,
    // the proxy cold-starts inside the solid and renders no force until the hand
    // exits and re-enters — objects are meant to be felt by approaching them.
    proxy_->initialize(world_, cVector3d(pos[0], pos[1], pos[2]));
    proxy_->setProxyRadius(tool_radius_);

    object_count_ = static_cast<int>(desired.size());
}

// Build an arbitrary triangle mesh from AI-authored vertices/indices.
static bool build_raw_mesh(cMesh* mesh, const HapticObjectSpec& o)
{
    const auto& v = o.vertices;
    const auto& tri = o.triangles;
    if (v.size() < 9 || v.size() % 3 != 0) return false;   // need >= 3 vertices
    if (tri.size() < 3 || tri.size() % 3 != 0) return false;
    const int nv = static_cast<int>(v.size() / 3);
    for (int i = 0; i < nv; ++i)
        mesh->newVertex(v[3 * i], v[3 * i + 1], v[3 * i + 2]);
    for (size_t i = 0; i + 2 < tri.size(); i += 3) {
        const int a = tri[i], b = tri[i + 1], c = tri[i + 2];
        if (a < 0 || b < 0 || c < 0 || a >= nv || b >= nv || c >= nv) continue;
        mesh->newTriangle(a, b, c);
    }
    if (mesh->getNumTriangles() == 0) return false;
    mesh->computeAllNormals();
    return true;
}

cMesh* HapticRenderer::build_solid(const HapticObjectSpec& o)
{
    cMesh* mesh = new cMesh();
    bool built = true;

    // `point` is the centre of every solid. CHAI3D builds spheres, boxes and
    // ellipsoids centred, but cylinders and cones with their base on the origin
    // extending to +z, so those two need offsetting back by -height/2.
    double z_offset = 0.0;

    if (o.type == "sphere") {
        cCreateSphere(mesh, std::max(1e-4, o.radius), 64, 64);
    } else if (o.type == "box") {
        cCreateBox(mesh, std::max(1e-4, 2.0 * o.size[0]),
                         std::max(1e-4, 2.0 * o.size[1]),
                         std::max(1e-4, 2.0 * o.size[2]));
    } else if (o.type == "cylinder") {
        const double h = std::max(1e-4, o.height);
        cCreateCylinder(mesh, h, std::max(1e-4, o.radius));
        z_offset = -0.5 * h;
    } else if (o.type == "ellipsoid") {
        cCreateEllipsoid(mesh, std::max(1e-4, o.size[0]),
                               std::max(1e-4, o.size[1]),
                               std::max(1e-4, o.size[2]));
    } else if (o.type == "cone") {
        const double h = std::max(1e-4, o.height);
        cCreateCone(mesh, h, std::max(1e-4, o.radius));
        z_offset = -0.5 * h;
    } else if (o.type == "mesh") {
        built = build_raw_mesh(mesh, o);
    } else {
        built = false;   // plane/spring/viscous/constant are not solids
    }
    if (!built) { delete mesh; return nullptr; }

    mesh->setLocalPos(o.point[0], o.point[1], o.point[2] + z_offset);
    mesh->m_material->setStiffness(clampd(o.stiffness, 0.0, max_stiffness_));
    if (o.friction > 0.0) {
        const double fr = clampd(o.friction, 0.0, 1.0);
        mesh->m_material->setUseHapticFriction(true);
        mesh->m_material->setStaticFriction(fr);
        mesh->m_material->setDynamicFriction(fr);
    }
    mesh->createAABBCollisionDetector(tool_radius_);
    return mesh;
}

void HapticRenderer::computeForce(const double pos[3], const double vel[3],
                                  double out[3])
{
    rebuildIfNeeded(pos);

    // 1) Geometry contact via the finger-proxy (god-object). Global positions
    // are computed once per scene (in rebuildIfNeeded), not per tick — the
    // objects are static, and re-transforming a fine mesh at 1 kHz would blow
    // the control-loop budget and cause buzzing.
    cVector3d f = proxy_->computeForces(cVector3d(pos[0], pos[1], pos[2]),
                                        cVector3d(vel[0], vel[1], vel[2]));
    double fx = f.x(), fy = f.y(), fz = f.z();

    // Contact damping: while actually touching a solid, oppose velocity to kill
    // the high-frequency buzz that pure stiffness contact produces on hardware.
    const double contact_mag2 = fx * fx + fy * fy + fz * fz;
    if (contact_mag2 > 1e-6) {
        fx -= contact_damping_ * vel[0];
        fy -= contact_damping_ * vel[1];
        fz -= contact_damping_ * vel[2];

        // Surface vibration texture: modulate the contact-normal force with a
        // sinusoid while touching a vibrating object (rough/buzzing feel).
        if (vib_amp_ > 0.0 && vib_freq_ > 0.0) {
            using namespace std::chrono;
            const double now = duration<double>(steady_clock::now().time_since_epoch()).count();
            if (t0_secs_ == 0.0) t0_secs_ = now;
            const double s = vib_amp_ * std::sin(2.0 * M_PI * vib_freq_ * (now - t0_secs_));
            const double inv = 1.0 / std::sqrt(contact_mag2);   // unit contact dir
            fx += s * fx * inv;
            fy += s * fy * inv;
            fz += s * fz * inv;
        }
    }

    // 2) Analytic half-space / field forces.
    for (const auto& o : analytic_)
    {
        const double k = clampd(o.stiffness, 0.0, max_stiffness_);
        if (o.type == "spring")
        {
            fx += k * (o.point[0] - pos[0]);
            fy += k * (o.point[1] - pos[1]);
            fz += k * (o.point[2] - pos[2]);
        }
        else if (o.type == "constant")
        {
            fx += o.force[0];
            fy += o.force[1];
            fz += o.force[2];
        }
        else if (o.type == "vibration")
        {
            // A cue, not an object: a zero-mean buzz felt wherever the hand is.
            // Surface vibration cannot do this — it modulates a contact force,
            // and a shell spawned around a stationary hand cold-starts inside
            // the solid, so it never touches anything.
            const double amp = clampd(o.vib_amp, 0.0, force_clamp_);
            const double f = clampd(o.vib_freq, 0.0, 500.0);
            if (amp > 0.0 && f > 0.0)
            {
                using namespace std::chrono;
                const double now =
                    duration<double>(steady_clock::now().time_since_epoch()).count();
                if (t0_secs_ == 0.0) t0_secs_ = now;
                const double w = 2.0 * M_PI * f * (now - t0_secs_);
                // Three phases, so the buzz is felt whichever way the handle is
                // held rather than only along one axis.
                fx += amp * std::sin(w);
                fy += amp * std::sin(w + 2.0 * M_PI / 3.0);
                fz += amp * std::sin(w + 4.0 * M_PI / 3.0);
            }
        }
        else if (o.type == "viscous")
        {
            const double b = clampd(o.damping, 0.0, 20.0);
            fx += -b * vel[0];
            fy += -b * vel[1];
            fz += -b * vel[2];
        }
        else if (o.type == "plane")
        {
            double nx = o.normal[0], ny = o.normal[1], nz = o.normal[2];
            const double nm = std::sqrt(nx * nx + ny * ny + nz * nz);
            if (nm > 1e-9)
            {
                nx /= nm; ny /= nm; nz /= nm;
                const double depth = (pos[0] - o.point[0]) * nx +
                                     (pos[1] - o.point[1]) * ny +
                                     (pos[2] - o.point[2]) * nz;
                if (depth < 0.0)  // hand crossed to the back of the plane
                {
                    fx += k * (-depth) * nx;
                    fy += k * (-depth) * ny;
                    fz += k * (-depth) * nz;
                }
            }
        }
    }

    // 3) Hard safety clamp on the total force magnitude.
    const double mag = std::sqrt(fx * fx + fy * fy + fz * fz);
    if (mag > force_clamp_ && mag > 1e-9)
    {
        const double s = force_clamp_ / mag;
        fx *= s; fy *= s; fz *= s;
    }
    out[0] = fx; out[1] = fy; out[2] = fz;
}

}  // namespace falcon_ros2
