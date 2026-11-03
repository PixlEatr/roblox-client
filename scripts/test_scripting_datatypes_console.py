"""New source datatypes through the console HTTP, owner thread, and native VM."""
import unittest
import test_browser_input as browser


class ScriptingDatatypesConsoleTests(unittest.TestCase):
    setUp=browser.BrowserKeyboardHTTPTests.setUp
    stop_worker=browser.BrowserKeyboardHTTPTests.stop_worker
    request=browser.BrowserKeyboardHTTPTests.request
    source=browser.BrowserKeyboardHTTPTests.source

    def test_http_execution_returns_real_typed_values_and_calendar_conversion(self):
        result=self.source('''
local t=DateTime.now():ToLocalTime()
local localDate=DateTime.fromLocalTime(t.Year,t.Month,t.Day,t.Hour,t.Minute,t.Second,t.Millisecond)
assert(localDate:ToLocalTime().Millisecond==t.Millisecond)
local cell=Vector3int16.new(10,20,30)+Vector3int16.new(2,-3,4)
return DateTime.fromIsoDate("2026-11-02T12:00:00+05:45"):ToIsoDate(),
 Vector2int16.new(4,5),cell,Region3int16.new(Vector3int16.new(),cell)
''')
        self.assertTrue(result.get('completed'),result)
        self.assertEqual(result['values'],['2026-11-02T06:15:00Z',
            {'$vector2int16':[4,5]},{'$vector3int16':[12,17,34]},
            {'$region3int16':[{'$vector3int16':[0,0,0]},{'$vector3int16':[12,17,34]}]}])


if __name__=='__main__':unittest.main()
