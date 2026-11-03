#pragma once

#include <cmath>

// Roblox compares each float32 component relative to the receiver. In
// particular FuzzyEq is intentionally asymmetric. Studio 0.745 reference
// cases are recorded in outputs/audit/motion-math-studio-reference.json.
inline bool runtime_fuzzy_component(double current, double other, double epsilon) {
    const float a=static_cast<float>(current), b=static_cast<float>(other);
    const float e=std::fabs(static_cast<float>(epsilon));
    return a==b || std::fabs(a-b)<=e*(1.0f+std::fabs(a));
}
