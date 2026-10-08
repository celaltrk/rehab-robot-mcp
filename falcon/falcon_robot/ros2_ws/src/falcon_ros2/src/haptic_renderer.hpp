// Written here, not in the original. See haptic_renderer.cpp.
#ifndef FALCON_ROS2__HAPTIC_RENDERER_HPP_
#define FALCON_ROS2__HAPTIC_RENDERER_HPP_

#include <mutex>
#include <string>
#include <vector>

// CHAI3D types are forward-declared so this header stays light; the full
// chai3d.h is only pulled into haptic_renderer.cpp.
namespace chai3d { class cWorld; class cAlgorithmFingerProxy; class cMesh; }

namespace falcon_ros2
{

// Plain, dependency-free description of one virtual object (mirrors the
// HapticObject message, decoupled from both ROS and CHAI3D).
struct HapticObjectSpec
{
    std::string type;
    double point[3]  = {0, 0, 0};
    double normal[3] = {0, 1, 0};
    double size[3]   = {0, 0, 0};
    double radius    = 0.0;
    double height    = 0.0;
    double stiffness = 0.0;
    double damping   = 0.0;
    double force[3]  = {0, 0, 0};
    // surface texture
    double friction  = 0.0;   // 0..1
    double vib_freq  = 0.0;   // Hz
    double vib_amp   = 0.0;   // N
    // arbitrary geometry (type == "mesh"), relative to point
    std::vector<double> vertices;   // flat x,y,z,...
    std::vector<int>    triangles;  // flat i0,i1,i2,...
};

// Renders a haptic scene into a Cartesian force.
//
//  * Closed solids (sphere/box/cylinder) become CHAI3D meshes rendered by the
//    finger-proxy (god-object: stable contact, no pop-through).
//  * Half-space / field types (plane/spring/viscous/constant) are cheap analytic
//    forces added on top.
//
// Thread model: requestScene() may be called from any thread; it only stores
// the desired scene. All CHAI3D state is touched exclusively by the thread that
// calls computeForce() (the 1 kHz haptic loop), which rebuilds the world lazily.
class HapticRenderer
{
public:
    // Falcon rated maxima (CHAI3D DHD_DEVICE_FALCON): 8 N, 3000 N/m.
    explicit HapticRenderer(double tool_radius = 0.003,
                            double force_clamp_n = 8.0,
                            double max_stiffness = 3000.0,
                            double contact_damping = 3.0);
    ~HapticRenderer();

    // Any thread: replace the desired scene (applied on the next computeForce).
    void requestScene(const std::vector<HapticObjectSpec>& objects);

    // Haptic thread only: force rendered at the given hand position/velocity.
    void computeForce(const double pos[3], const double vel[3], double out[3]);

    int objectCount();

private:
    void rebuildIfNeeded(const double pos[3]);   // haptic thread only
    // Build a CHAI3D mesh for a solid object (sphere/box/cylinder/ellipsoid/
    // cone/mesh), with stiffness + friction applied. Returns nullptr for
    // non-solid (analytic) types.
    chai3d::cMesh* build_solid(const HapticObjectSpec& o);

    chai3d::cWorld* world_ = nullptr;
    chai3d::cAlgorithmFingerProxy* proxy_ = nullptr;
    double tool_radius_;
    double force_clamp_;
    double max_stiffness_;
    double contact_damping_;

    std::mutex mutex_;                         // guards pending_/dirty_
    std::vector<HapticObjectSpec> pending_;    // desired scene (writer: any thread)
    bool dirty_ = false;

    std::vector<HapticObjectSpec> analytic_;   // non-geometry objects (haptic thread)
    int object_count_ = 0;

    // active surface vibration (strongest vibrating solid in the scene)
    double vib_freq_ = 0.0;
    double vib_amp_ = 0.0;
    double t0_secs_ = 0.0;   // wall-clock origin for the vibration phase
};

}  // namespace falcon_ros2

#endif  // FALCON_ROS2__HAPTIC_RENDERER_HPP_
