"""DateTime scripting parity against saved Studio probes and controlled time zones."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_motion_math as motion

ROOT=Path(__file__).resolve().parents[2]
REFERENCE=json.loads((ROOT/'tests/fixtures/references/datetime-local-studio-reference.json').read_text())


@unittest.skipUnless(motion.RUST_HOST.is_file(), 'build the persistent worker first')
class DateTimeLocalTests(unittest.TestCase):
    execute=motion.MotionMathTests.execute

    def setUp(self):
        # Studio's captured current device offset was UTC-08:00. Keep the
        # comparison deterministic even when the developer travels or DST changes.
        with patch.dict(os.environ, {'TZ':'Etc/GMT+8'}):
            self.host=motion.PersistentLuauHost(motion.SNAPSHOT)
        self.addCleanup(self.host.close)

    def test_iso_acceptance_and_timestamps_match_all_saved_native_cases(self):
        cases=[c for p in REFERENCE['probes'] for c in p['value'].get('iso',[])]
        self.execute('local cases=game:GetService("HttpService"):JSONDecode([==['+json.dumps(cases)+']==])\n'+'''
for i,c in ipairs(cases)do
 local ok,d=pcall(DateTime.fromIsoDate,c.input)
 assert(ok==c.ok,"ISO acceptance "..i)
 assert((ok and d and d.UnixTimestampMillis or false)==c.millis,"ISO timestamp "..i.." "..c.input)
end
''')

    def test_local_constructor_and_components_match_native_including_current_offset(self):
        cases=REFERENCE['probes'][0]['value']['localTime']
        self.execute('local cases=game:GetService("HttpService"):JSONDecode([==['+json.dumps(cases)+']==])\n'+'''
for i,c in ipairs(cases)do
 local ok,d=pcall(DateTime.fromLocalTime,unpack(c.args))
 assert(ok==c.ok,"local acceptance "..i)
 if ok then
  assert(d.UnixTimestampMillis==c.millis,"local timestamp "..i)
  local t=d:ToLocalTime()
  for k,v in pairs(c.components)do assert(t[k]==v,"local component "..i.." "..k)end
 end
end
''')

    def test_utc_constructor_duration_sign_matches_native(self):
        cases=REFERENCE['probes'][1]['value']['cases']
        self.execute('local cases=game:GetService("HttpService"):JSONDecode([==['+json.dumps(cases)+']==])\n'+'''
for i,c in ipairs(cases)do
 for _,name in ipairs({"fromUniversalTime","fromLocalTime"})do
  local ok,v=pcall(function()return DateTime[name](unpack(c.args)):ToIsoDate()end)
  assert(ok==c[name].ok and v==c[name].value,"duration sign "..i.." "..name)
 end
end
''')

    def test_string_conversion_and_iso_precision_match_native(self):
        cases=REFERENCE['probes'][2]['value']['values']
        self.execute('local cases=game:GetService("HttpService"):JSONDecode([==['+json.dumps(cases)+']==])\n'+'''
for _,c in ipairs(cases)do
 local d=DateTime.fromUnixTimestampMillis(c.ms)
 assert(tostring(d)==c.text and d:ToIsoDate()==c.iso)
 assert(d.UnixTimestampMillis==c.ms)
end
''')

    def test_date_bounds_type_validation_and_local_wall_time_range(self):
        self.execute('''
for _,args in ipairs({{1900,2,29},{2020,13,1},{2020,1,32},{1399},{10000},{math.huge},{0/0},{true}})do
 assert(not pcall(DateTime.fromLocalTime,unpack(args)))
end
assert(not pcall(function()DateTime.now().UnixTimestamp=0 end))
assert(not pcall(function()DateTime.fromLocalTime=function()end end))
assert(not pcall(function()return DateTime.fromUnixTimestampMillis(-17987443200000):ToLocalTime()end))
local upper=DateTime.fromLocalTime(9999,12,31,23,59,59,999)
assert(upper:ToLocalTime().Year==9999)
assert(not pcall(function()return upper:ToUniversalTime()end))
assert(not pcall(function()return upper:ToIsoDate()end))
assert(not pcall(DateTime.fromIsoDate,{}))
assert(DateTime.fromIsoDate(string.rep("9",5000))==nil)
''')

    def test_utc_and_non_hour_timezones_roundtrip_negative_epoch_and_leap_day(self):
        for timezone,offset in [('UTC',0),('Etc/GMT+8',-28800),('Asia/Kathmandu',20700)]:
            with self.subTest(timezone=timezone):
                self.host.close()
                with patch.dict(os.environ, {'TZ':timezone}):
                    self.host=motion.PersistentLuauHost(motion.SNAPSHOT)
                self.addCleanup(self.host.close)
                self.execute(f'local offset={offset}\n'+'''
assert(DateTime.fromLocalTime().UnixTimestampMillis==-offset*1000)
for _,ms in ipairs({-1,0,951827696789,1793639017123})do
 local d=DateTime.fromUnixTimestampMillis(ms)
 local t=d:ToLocalTime()
 local round=DateTime.fromLocalTime(t.Year,t.Month,t.Day,t.Hour,t.Minute,t.Second,t.Millisecond)
 assert(round==d)
 local utc=DateTime.fromUniversalTime(t.Year,t.Month,t.Day,t.Hour,t.Minute,t.Second,t.Millisecond)
 assert(utc.UnixTimestampMillis==ms+offset*1000)
 t.Year=1;assert(d:ToLocalTime().Year~=1)
end
''')

    def test_existing_datetime_script_and_new_module_scheduling_demo(self):
        self.execute((ROOT/'tests/fixtures/datetime_bindings.luau').read_text())
        result=self.execute((ROOT/'runtime/scripts/datetime_scripting_demo.luau').read_text())
        self.assertEqual(result['values'],['DateTime scripting completed',True,True])


if __name__=='__main__':unittest.main()
