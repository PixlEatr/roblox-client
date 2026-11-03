"""Regression for reading Health on a received Humanoid with omitted fields."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock
import test_cframe_transforms as transforms


def missing_health_snapshot():
    snapshot=copy.deepcopy(transforms.SNAPSHOT)
    humanoid=next(n for n in snapshot['nodes'] if n['class_name']=='Humanoid')
    for key in ('Health','MaxHealth'):
        humanoid['properties'].pop(key,None)
        humanoid.get('origins',{}).pop(key,None)
    return snapshot


class HealthDefaultTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def test_missing_fields_read_constructor_defaults_without_mutations(self):
        snapshot=missing_health_snapshot();before=copy.deepcopy(snapshot)
        for result in self.run_script('''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
assert(h.Health==100 and h.MaxHealth==100)
assert(client.propertyOrigin(h,"Health")=="class_default")
assert(client.propertyOrigin(h,"MaxHealth")=="class_default")
assert(not pcall(function()return h.HipHeight end))
''',snapshot):
            self.assertFalse([m for m in result['mutations'] if m.get('property') in ('Health','MaxHealth')])
        self.assertEqual(snapshot,before)

    def test_received_values_including_zero_override_defaults(self):
        for health,maximum in [(37,80),(0,100),(250,300)]:
            snapshot=missing_health_snapshot()
            humanoid=next(n for n in snapshot['nodes'] if n['class_name']=='Humanoid')
            humanoid['properties'].update(Health=health,MaxHealth=maximum)
            self.run_script('''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
assert(h.Health==%s and h.MaxHealth==%s)
assert(client.propertyOrigin(h,"Health")=="received")
assert(client.propertyOrigin(h,"MaxHealth")=="received")
''' % (health,maximum),snapshot)

    def test_damage_and_healing_work_from_omitted_initial_health(self):
        self.run_script('''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
local changes={};h.HealthChanged:Connect(function(value)table.insert(changes,value)end)
h:TakeDamage(25);assert(h.Health==75)
h:TakeDamage(-10);assert(h.Health==85)
h.MaxHealth=120;h.Health=200;assert(h.Health==120)
assert(client.propertyOrigin(h,"Health")=="local")
task.wait();assert(#changes==4)
assert(changes[1]==75 and changes[2]==85 and changes[3]==85 and changes[4]==120)
''',missing_health_snapshot())


class PersistentHealthDefaultTests(unittest.TestCase):
    def setUp(self):
        import persistent_runtime
        path=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(persistent_runtime,'RUST_HOST',path):
            self.host=persistent_runtime.PersistentLuauHost(missing_health_snapshot())
        self.addCleanup(self.host.close)

    def execute(self,source):
        result=self.host.call({'op':'execute','source':source})
        self.assertTrue(result['ok'],result)
        self.assertFalse(result.get('task_errors'),result)
        return result

    def test_received_death_supersedes_default_and_fires_once(self):
        self.execute('''
h=game:GetService("Players").LocalPlayer.Character.Humanoid
assert(h.Health==100)
healths={};deaths=0
h.HealthChanged:Connect(function(v)table.insert(healths,v)end)
h.Died:Connect(function()deaths+=1 end)
''')
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':8,'actions':[
            {'sequence':8,'kind':'set','id':'scope:humanoid-1','fields':{'Health':0}}]}})
        self.assertTrue(result['ok'],result)
        self.assertFalse([m for m in result.get('mutations',[]) if m.get('property')=='Health'])
        self.execute('''
assert(h.Health==0 and deaths==1 and #healths==1 and healths[1]==0)
assert(client.propertyOrigin(h,"Health")=="received")
h.Health=50;assert(h.Health==0 and deaths==1)
''')

    def test_later_receipt_overrides_locally_damaged_default(self):
        self.execute('h=game:GetService("Players").LocalPlayer.Character.Humanoid;h:TakeDamage(25);assert(h.Health==75)')
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':8,'actions':[
            {'sequence':8,'kind':'set','id':'scope:humanoid-1','fields':{'Health':60}}]}})
        self.assertTrue(result['ok'],result)
        self.execute('assert(h.Health==60 and client.propertyOrigin(h,"Health")=="received")')

if __name__=='__main__':unittest.main()
