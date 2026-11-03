--!optimize 2

local globalenv = getgenv and getgenv() or _G or shared
local globalcontainer = globalenv.globalcontainer

if not globalcontainer then
	globalcontainer = {}
	globalenv.globalcontainer = globalcontainer
end

local genvs = {}
if getgenv then table.insert(genvs, getgenv()) end
table.insert(genvs, shared)
table.insert(genvs, _G)
-- if getrenv then -- Add this if you wish to search through game's env (normal Scripts)
-- 	table.insert(genvs, 1, getrenv())
-- end
-- if debug and debug.getregistry then
-- 	table.insert(genvs, 1, debug.getregistry()._LOADED) -- Includes things like string / table / math library tables etc. Basically everything roblox offers, but due to being too huge you should specify library tables by attaching ".string" or to LOADED; adding "_G" to this will link a globals table. That way the finder will have to scan way less.
-- end

local calllimit = 0
do
	local function determineCalllimit()
		calllimit = calllimit + 1
		determineCalllimit()
	end
	pcall(determineCalllimit)
end

local function isEmpty(dict)
	for _ in next, dict do
		return
	end
	return true
end

local depth, printresults, hardlimit, query, antioverflow, matchedall -- prevents infinite / cross-reference
local function recurseEnv(env, envname)
	if globalcontainer == env then
		return
	end
	if antioverflow[env] then
		return
	end
	antioverflow[env] = true

	depth = depth + 1
	for name, val in next, env do
		if matchedall then
			break
		end

		local Type = type(val)

		if Type == "table" then
			if depth < hardlimit then
				recurseEnv(val, name)
			else
				-- warn("almost stack overflow")
			end
		elseif Type == "function" then -- This optimizes the speeds but if someone manages (??) to fool this then rip
			name = string.lower(tostring(name))
			local matched
			for methodname, pattern in next, query do
				if pattern(name, envname) then
					globalcontainer[methodname] = val
					if not matched then
						matched = {}
					end
					table.insert(matched, methodname)
					if printresults then
						print(methodname, name)
					end
				end
			end
			if matched then
				for _, methodname in next, matched do
					query[methodname] = nil
				end
				matchedall = isEmpty(query)
				if matchedall then
					break
				end
			end
		end
	end
	depth = depth - 1
end

local function finder(Query, ForceSearch, CustomCallLimit, PrintResults)
	antioverflow = {}
	query = {}

	do -- Load patterns
		local function Find(String, Pattern)
			return string.find(String, Pattern, nil, true)
		end
		for methodname, pattern in next, Query do
			if not globalcontainer[methodname] or ForceSearch then
				if not Find(pattern, "return") then
					pattern = "return " .. pattern
				end
				query[methodname] = loadstring(pattern)
			end
		end
	end

	depth = 0
	printresults = PrintResults
	hardlimit = CustomCallLimit or calllimit

	recurseEnv(genvs)

	-- fallback to __index inheritance
	do
		local env = getfenv()
		for methodname in next, Query do
			if not globalcontainer[methodname] then
				globalcontainer[methodname] = env[methodname]
			end
		end
	end

	hardlimit = nil
	depth = nil
	printresults = nil

	antioverflow = nil
	query = nil
end

return finder, globalcontainer
