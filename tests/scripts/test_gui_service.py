"""Local desktop GUI service behavior through the persistent native VM."""
import unittest

from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT


class GuiServiceTests(unittest.TestCase):
    def host(self):
        host=PersistentLuauHost(SNAPSHOT)
        self.addCleanup(host.close)
        return host

    def execute(self,host,source):
        result=host.call({'op':'execute','source':source,'timeout_ms':100})
        self.assertTrue(result['ok'],result)
        return result['values']

    def test_local_service_is_canonical_and_detects_desktop_interface(self):
        values=self.execute(self.host(),'''
local absent=game:FindService("GuiService")==nil
local gui=game:GetService("GuiService")
return absent,gui==game:GetService("GuiService"),gui==game:FindService("GuiService"),
    gui.Parent==game,gui.ClassName,gui:IsTenFootInterface()
''')
        self.assertEqual(values,[True,True,True,True,'GuiService',False])

    def test_method_rejects_wrong_receiver_and_service_cannot_be_constructed(self):
        values=self.execute(self.host(),'''
local gui=game:GetService("GuiService")
return pcall(function()gui.IsTenFootInterface(workspace)end),
    pcall(function()Instance.new("GuiService")end)
''')
        self.assertFalse(values[0])
        self.assertFalse(values[1])

    def test_module_can_discover_gui_service_and_choose_desktop_branch(self):
        host=self.host()
        values=self.execute(host,'''
local services={}
for _,name in {"GuiService","ServerStorage"} do
    local ok,service=pcall(game.GetService,game,name)
    if ok then services[name]=service end
end
return services.GuiService:IsTenFootInterface() and "console" or "desktop"
''')
        self.assertEqual(values,['desktop'])


if __name__=='__main__':unittest.main()
