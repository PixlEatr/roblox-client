// Ordinary standalone Random compatibility port.
// Provenance: user-supplied native-overrides/src/runtime_v2.cpp lines 6371-6504
// from the user's Env Logger 2.0 workspace. The source uses a PCG XSH-RR stream
// (multiplier 6364136223846793005, increment 105), a two-step seed initializer,
// and the measured extra draw for the two widest int32 intervals.
//
// Historical scope: the old runtime/release-729 source and independent recorded
// Studio 0.742.0.7421053 fixtures. Roblox build 0.745 compatibility is UNVERIFIED.
// Official Random documentation says fractional seeds round down; those 0.742
// controls and the supplied source instead truncate toward zero. Preserve that
// measured behavior here. Bounds likewise truncate toward zero before sampling.
//
// Deliberate standalone constraints: exact argument counts, finite arguments,
// signed-int32 integer bounds, and no simulated game/service/environment APIs.
// An omitted/nil seed uses actual host /dev/urandom entropy, replacing the old
// runtime's deterministic context seed/sequence. No authentication code is used.
#include "random_compat.hpp"

#include "lua.h"
#include "lualib.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <limits>

namespace
{
constexpr uint64_t kMultiplier = UINT64_C(6364136223846793005);
constexpr uint64_t kIncrement = UINT64_C(105);
constexpr uint64_t kMaximumExactSeed = UINT64_C(9007199254740991);
constexpr const char* kMetatable = "RobloxClientResearch.RandomCompatibility.v1";

struct RandomState
{
    uint64_t state;
};

void checkArity(lua_State* L, int expected, const char* method)
{
    if (lua_gettop(L) != expected)
        luaL_error(L, "%s expects %d argument(s)", method, expected - 1);
}

double finiteNumber(lua_State* L, int index)
{
    luaL_checktype(L, index, LUA_TNUMBER);
    const double value = lua_tonumber(L, index);
    if (!std::isfinite(value))
        luaL_argerror(L, index, "number must be finite");
    return value;
}

int32_t integerBound(lua_State* L, int index)
{
    const double value = std::trunc(finiteNumber(L, index));
    if (value < static_cast<double>(std::numeric_limits<int32_t>::min()) ||
        value > static_cast<double>(std::numeric_limits<int32_t>::max()))
        luaL_argerror(L, index, "integer bound is outside signed int32");
    return static_cast<int32_t>(value);
}

RandomState* stateFor(lua_State* L)
{
    return static_cast<RandomState*>(luaL_checkudata(L, 1, kMetatable));
}

RandomState* pushRandom(lua_State* L, uint64_t state)
{
    auto* result = static_cast<RandomState*>(lua_newuserdata(L, sizeof(RandomState)));
    result->state = state;
    luaL_getmetatable(L, kMetatable);
    lua_setmetatable(L, -2);
    return result;
}

uint64_t normalizedSeed(lua_State* L, double seed)
{
    constexpr double limit = static_cast<double>(kMaximumExactSeed);
    if (!std::isfinite(seed) || seed < -limit || seed > limit)
        luaL_error(L, "Random seed must be finite and within +/- (2^53 - 1)");

    uint64_t state = kIncrement; // Advance from zero once.
    state += static_cast<uint64_t>(static_cast<int64_t>(std::trunc(seed)));
    return state * kMultiplier + kIncrement;
}

bool readHostSeed(double& seed)
{
    // Do not substitute wall time or a deterministic fallback if entropy fails.
    // No Luau error is raised while this C++ stream object is alive.
    std::ifstream source("/dev/urandom", std::ios::in | std::ios::binary);
    uint64_t bits = 0;
    if (!source.read(reinterpret_cast<char*>(&bits), sizeof(bits)))
        return false;
    seed = static_cast<double>(bits & kMaximumExactSeed);
    return true;
}

uint32_t nextUint32(RandomState& random)
{
    const uint64_t previous = random.state;
    random.state = previous * kMultiplier + kIncrement;
    const uint32_t shifted = static_cast<uint32_t>(((previous >> 18u) ^ previous) >> 27u);
    const uint32_t rotation = static_cast<uint32_t>(previous >> 59u);
    return (shifted >> rotation) | (shifted << ((0u - rotation) & 31u));
}

double nextFraction(RandomState& random)
{
    const uint32_t low = nextUint32(random);
    const uint32_t high = nextUint32(random);
    const uint64_t bits = static_cast<uint64_t>(low) | (static_cast<uint64_t>(high) << 32u);
    return std::ldexp(static_cast<double>(bits), -64);
}

int randomNew(lua_State* L)
{
    const int count = lua_gettop(L);
    if (count > 1)
        luaL_error(L, "Random.new expects zero or one argument");
    double seed = 0;
    if (count == 0 || lua_isnil(L, 1))
    {
        if (!readHostSeed(seed))
            luaL_error(L, "Random.new could not read host entropy");
    }
    else
        seed = finiteNumber(L, 1);
    pushRandom(L, normalizedSeed(L, seed));
    return 1;
}

int randomClone(lua_State* L)
{
    checkArity(L, 1, "Random:Clone");
    const uint64_t state = stateFor(L)->state;
    pushRandom(L, state);
    return 1;
}

int randomNextNumber(lua_State* L)
{
    const int count = lua_gettop(L);
    if (count != 1 && count != 3)
        luaL_error(L, "Random:NextNumber expects zero or two arguments");
    RandomState* random = stateFor(L);
    if (count == 1)
    {
        lua_pushnumber(L, nextFraction(*random));
        return 1;
    }
    double minimum = finiteNumber(L, 2);
    double maximum = finiteNumber(L, 3);
    if (minimum > maximum)
        std::swap(minimum, maximum);
    const double fraction = nextFraction(*random);
    // Weighted endpoints avoid overflowing (maximum - minimum) for finite
    // opposite-sign endpoints. Preserve the supplied source's evaluation order.
    double result = minimum * (1.0 - fraction) + maximum * fraction;
    if (result < minimum)
        result = minimum;
    else if (result > maximum)
        result = maximum;
    lua_pushnumber(L, result);
    return 1;
}

int randomNextInteger(lua_State* L)
{
    checkArity(L, 3, "Random:NextInteger");
    RandomState* random = stateFor(L);
    int64_t minimum = integerBound(L, 2);
    int64_t maximum = integerBound(L, 3);
    if (minimum > maximum)
        std::swap(minimum, maximum);
    const uint64_t width = static_cast<uint64_t>(maximum - minimum) + 1u;
    if (width >= UINT64_C(4294967295))
        (void)nextUint32(*random);
    const uint64_t scaled = width * static_cast<uint64_t>(nextUint32(*random));
    // Signed int32 endpoints guarantee this result is representable by Luau's
    // ordinary integer push API, including full-width and reversed intervals.
    const int64_t result = minimum + static_cast<int64_t>(scaled >> 32u);
    lua_pushinteger(L, static_cast<int>(result));
    return 1;
}

int randomToString(lua_State* L)
{
    checkArity(L, 1, "Random tostring");
    (void)stateFor(L);
    lua_pushstring(L, "Random");
    return 1;
}

void setFunction(lua_State* L, const char* name, lua_CFunction function)
{
    lua_pushcfunction(L, function, name);
    lua_setfield(L, -2, name);
}
} // namespace

void install_random_compat(lua_State* L)
{
    if (luaL_newmetatable(L, kMetatable))
    {
        lua_newtable(L);
        setFunction(L, "NextInteger", randomNextInteger);
        setFunction(L, "NextNumber", randomNextNumber);
        setFunction(L, "Clone", randomClone);
        lua_setreadonly(L, -1, true);
        lua_setfield(L, -2, "__index");
        setFunction(L, "__tostring", randomToString);
        lua_pushstring(L, "Random");
        lua_setfield(L, -2, "__type");
        lua_pushstring(L, "Random");
        lua_setfield(L, -2, "__metatable");
        lua_setreadonly(L, -1, true);
    }
    lua_pop(L, 1);

    lua_newtable(L);
    setFunction(L, "new", randomNew);
    lua_setreadonly(L, -1, true);
    lua_setglobal(L, "Random");
}
