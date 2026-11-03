"""Bounded stair and slope helpers for the local root-box movement proxy.

This is a conservative static-box approximation. It requires a walkable landing
surface, limits rise, and sweeps the proxy through both the raising and forward
parts of a step so a step cannot cross a low ceiling or a tall wall.
"""
import math


def walkable_normal(normal, max_slope_angle=89.0):
    """Whether an upward surface normal is within a Humanoid slope limit."""
    if (not isinstance(normal, (tuple, list)) or len(normal) != 3
            or isinstance(max_slope_angle, bool)
            or not isinstance(max_slope_angle, (int, float))
            or not math.isfinite(max_slope_angle) or not 0 <= max_slope_angle <= 89):
        return False
    try:
        x, y, z = (float(v) for v in normal)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in (x, y, z)) or y <= 0:
        return False
    length = math.sqrt(x*x + y*y + z*z)
    return length > 0 and y / length + 1e-12 >= math.cos(math.radians(max_slope_angle))


def slope_slide_acceleration(normal, gravity=196.2):
    """Gravity component tangent to a steep support plane (world units/s²).

    Returns zero for invalid normals or nonpositive upward-facing normals. The
    caller applies this only when grounded on a surface that exceeds its slope
    limit; vertical integration alone cannot represent Roblox's slide state.
    """
    if (isinstance(gravity, bool) or not isinstance(gravity, (int, float))
            or not math.isfinite(gravity) or gravity < 0):
        return (0., 0., 0.)
    try:
        nx, ny, nz = (float(v) for v in normal)
    except (TypeError, ValueError):
        return (0., 0., 0.)
    length = math.sqrt(nx*nx + ny*ny + nz*nz)
    if not math.isfinite(length) or length <= 0 or ny <= 0:
        return (0., 0., 0.)
    nx, ny, nz = nx/length, ny/length, nz/length
    return (gravity*nx*ny, -gravity*(1-ny*ny), gravity*nz*ny)


def try_step_up(position, displacement, half_size, geometry, *, foot_offset=3.0,
                max_step_height=1.0, max_slope_angle=89.0, clearance=0.05):
    """Return a safe stepped destination, or None if no bounded step is valid.

    ``position`` and ``displacement`` are root-center vectors. The proposed
    endpoint must have a support hit no more than ``max_step_height`` above the
    starting foot plane. An upward body sweep and a raised horizontal sweep
    provide head clearance and reject intervening/tall walls.
    """
    from physics_body_collision import _first_hit, SKIN
    from physics_ground import ground_raycast

    vals = tuple(position) + tuple(displacement) + tuple(half_size)
    if len(vals) != 9 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                 and math.isfinite(v) for v in vals):
        return None
    if (not math.isfinite(max_step_height) or max_step_height <= 0
            or not math.isfinite(foot_offset) or foot_offset < 0
            or not math.isfinite(clearance) or clearance < 0):
        return None
    x, y, z = position
    dx, _, dz = displacement
    start_support = ground_raycast(position, geometry, foot_offset=foot_offset, max_distance=.05)
    if start_support is None or not walkable_normal(start_support.normal, max_slope_angle):
        return None
    landing_probe = (x+dx, y+max_step_height+clearance, z+dz)
    landing = ground_raycast(landing_probe, geometry, foot_offset=foot_offset,
                             max_distance=max_step_height+2*clearance)
    if landing is None or not walkable_normal(landing.normal, max_slope_angle):
        return None
    rise = landing.y - (y-foot_offset)
    if rise <= 1e-7 or rise > max_step_height + 1e-8:
        return None
    # Move up while still behind the obstacle, then sweep forward at the new
    # height. The second sweep prevents the step from passing through a taller
    # obstacle that happens to be underneath the landing ray.
    up = (0., rise+clearance, 0.)
    if _first_hit(position, half_size, up, geometry) is not None:
        return None
    raised = (x, y+rise+clearance, z)
    forward = (dx, 0., dz)
    if _first_hit(raised, half_size, forward, geometry) is not None:
        return None
    dest = (x+dx, landing.y+foot_offset, z+dz)
    # Confirm the destination still has local support, including finite edges.
    support = ground_raycast(dest, geometry, foot_offset=foot_offset,
                             max_distance=clearance+1e-5)
    if support is None or not walkable_normal(support.normal, max_slope_angle):
        return None
    return dest
