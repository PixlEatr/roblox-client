-- Run after the native DateTime bindings have been installed.
local epoch = DateTime.fromUniversalTime()
assert(epoch.UnixTimestampMillis == 0)
assert(epoch.UnixTimestamp == 0)
assert(epoch:ToIsoDate() == "1970-01-01T00:00:00Z")

local leap = DateTime.fromUniversalTime(2000, 2, 29, 12, 34, 56, 789)
assert(leap:ToIsoDate() == "2000-02-29T12:34:56Z")
local parts = leap:ToUniversalTime()
assert(parts.Year == 2000 and parts.Month == 2 and parts.Day == 29)
assert(parts.Hour == 12 and parts.Minute == 34 and parts.Second == 56)
assert(parts.Millisecond == 789)

local roundTrip = DateTime.fromIsoDate(leap:ToIsoDate())
assert(roundTrip.UnixTimestampMillis == leap.UnixTimestampMillis - 789)
assert(DateTime.fromUnixTimestampMillis(leap.UnixTimestampMillis) == leap)
assert(DateTime.fromUnixTimestamp(-0.0001).UnixTimestampMillis == 0)
assert(DateTime.fromUnixTimestamp(1.9).UnixTimestampMillis == 1000)
assert(DateTime.fromUnixTimestamp(-1.9).UnixTimestampMillis == -1000)
assert(DateTime.fromIsoDate("1900-02-29T00:00:00Z") == nil)
assert(DateTime.fromIsoDate("2020-01-02T03:04:05+00:00").UnixTimestampMillis == 1577934245000)
assert(not pcall(function() DateTime.fromUniversalTime(1900, 2, 29) end))
assert(not pcall(function() DateTime.fromUnixTimestampMillis(253402300800000) end))

-- Time components outside the normal range roll across dates as Roblox's UTC
-- constructor documents; date components themselves must be valid.
assert(DateTime.fromUniversalTime(2020, 1, 1, 24):ToIsoDate() == "2020-01-02T00:00:00Z")
return true
