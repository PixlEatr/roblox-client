"""Health scripts executed by both compiled workers; no live session."""
import unittest
import test_cframe_transforms as transforms

class HumanoidHealthTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script
    setup='''
local model=Instance.new("Model",workspace)
local root=Instance.new("Part",model);root.Name="HumanoidRootPart"
local h=Instance.new("Humanoid",model)
'''

    def test_damage_healing_and_health_limits(self):
        self.run_script(self.setup+'''
assert(h.Health==100 and h.MaxHealth==100)
assert(select('#',h:TakeDamage(25))==0);assert(h.Health==75)
h:TakeDamage(-10);assert(h.Health==85)
h:TakeDamage(-1000);assert(h.Health==100)
h.Health=200;assert(h.Health==100)
h.MaxHealth=60;assert(h.Health==60 and h.MaxHealth==60)
h.MaxHealth=120;assert(h.Health==60)
''')

    def test_health_events_and_death_lock(self):
        self.run_script(self.setup+'''
local healths={};local deaths=0
h.HealthChanged:Connect(function(v)table.insert(healths,v)end)
h.Died:Connect(function()deaths+=1 end)
h:TakeDamage(20);h.Health=50;h.MaxHealth=40;h.MaxHealth=80
h:TakeDamage(1000);h:TakeDamage(1000);h:TakeDamage(-100);h.Health=70
assert(h.Health==0);task.wait()
assert(#healths==5, #healths)
assert(healths[1]==80 and healths[2]==50 and healths[3]==40 and healths[4]==40 and healths[5]==0)
assert(deaths==1)
''')

    def test_forcefield_scopes_visible_false_and_direct_write(self):
        self.run_script(self.setup+'''
local f=Instance.new("ForceField",model);f.Visible=false
h:TakeDamage(20);assert(h.Health==100)
h.Health=70;h:TakeDamage(-10);assert(h.Health==70)
f.Parent=root;h:TakeDamage(20);assert(h.Health==70)
local ancestor=Instance.new("Folder",workspace);model.Parent=ancestor
f.Parent=ancestor;h:TakeDamage(20);assert(h.Health==70)
f.Parent=workspace;h:TakeDamage(20);assert(h.Health==50)
f.Parent=h;h:TakeDamage(10);assert(h.Health==40)
f.Parent=model;f:Destroy();h:TakeDamage(10);assert(h.Health==30)
''')

    def test_unrelated_or_detached_forcefields_do_not_protect(self):
        self.run_script(self.setup+'''
local unrelated=Instance.new("Model",workspace)
local f=Instance.new("ForceField",unrelated)
h:TakeDamage(10);assert(h.Health==90)
f.Parent=nil;model.Parent=nil;h:TakeDamage(10);assert(h.Health==80)
''')

    def test_damage_copied_method_uses_actual_receiver(self):
        self.run_script(self.setup+'''
local second=Instance.new("Humanoid",model)
local damage=h.TakeDamage;damage(second,20)
assert(second.Health==80 and h.Health==100)
assert(not pcall(damage,root,20));assert(h.Health==100)
''')

    def test_invalid_inputs_preserve_health_and_events(self):
        self.run_script(self.setup+'''
local changes=0;h.HealthChanged:Connect(function()changes+=1 end)
for _,v in {false, "10", {}, 0/0, math.huge, -math.huge} do
 assert(not pcall(function()h:TakeDamage(v)end))
 assert(not pcall(function()h.Health=v end))
 assert(not pcall(function()h.MaxHealth=v end))
end
assert(h.Health==100 and h.MaxHealth==100)
h:TakeDamage(0);h.Health=100;h.MaxHealth=100;task.wait();assert(changes==0)
''')

    def test_death_outside_workspace_does_not_fire_died(self):
        self.run_script(self.setup+'''
model.Parent=nil;local deaths=0;local healths=0
h.Died:Connect(function()deaths+=1 end)
h.HealthChanged:Connect(function()healths+=1 end)
h:TakeDamage(100);task.wait();assert(deaths==0 and healths==1)
model.Parent=workspace;h.Health=50;task.wait();assert(h.Health==0 and deaths==0)
''')

    def test_received_health_without_maximum_uses_constructor_maximum(self):
        self.run_script('''local h=game:GetService("Players").LocalPlayer.Character.Humanoid
h:TakeDamage(20);assert(h.Health==80)
h:TakeDamage(-10);assert(h.Health==90 and h.MaxHealth==100)
assert(client.propertyOrigin(h,"MaxHealth")=="class_default")
h.MaxHealth=150;h:TakeDamage(-100);assert(h.Health==150)
''')

    def test_extreme_finite_healing_saturates_without_overflow(self):
        self.run_script(self.setup+'''
h.MaxHealth=1e308;h.Health=9e307
h:TakeDamage(-1e308);assert(h.Health==1e308)
h:TakeDamage(1e308);assert(h.Health==0)
''')

    def test_lethal_damage_prevents_a_following_local_jump(self):
        import copy
        from physics_script_bridge import PhysicsScriptBridge
        from physics_controller import OwnCharacterPhysicsController
        from physics_ground import AABB,GroundGeometry
        snap=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in snap['nodes'] if n['name']=='HumanoidRootPart')
        root['properties'].update(Anchored=False,Position={'$vector3':[0,3,0]},Size={'$vector3':[2,2,1]})
        root.pop('origins',None)
        geometry=GroundGeometry((AABB((-20,-1,-20),(20,0,20)),),())
        for result in self.run_script('''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
h:TakeDamage(100);h.Jump=true;assert(h.Health==0)
''',snap):
            bridge=PhysicsScriptBridge(lambda s:OwnCharacterPhysicsController(s,geometry=geometry))
            bridge.update(snap)
            outcomes=bridge.consume(result['movement_intents'])
            self.assertEqual(len(outcomes),1)
            self.assertFalse(outcomes[0]['accepted'])
            self.assertEqual(outcomes[0]['reason'],'humanoid_not_alive')
            self.assertTrue(bridge.step(.1)['predicted_state']['grounded'])

    def test_dead_received_humanoid_ignores_positive_health_without_a_maximum(self):
        self.run_script('''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
h:TakeDamage(100);assert(h.Health==0)
h.Health=50;assert(h.Health==0)
h:TakeDamage(-50);assert(h.Health==0)
''')

    def test_destroyed_receiver_rejected(self):
        self.run_script(self.setup+'''
local damage=h.TakeDamage;h:Destroy()
assert(not pcall(damage,h,5))
assert(not pcall(function()h.Health=10 end))
''')

    def test_mutation_output_contains_clamped_health_only(self):
        for result in self.run_script(self.setup+'h:TakeDamage(200)'):
            health=[m for m in result['mutations'] if m.get('property')=='Health']
            self.assertEqual([m['value'] for m in health],[0])

class ReceivedHealthTests(unittest.TestCase):
    def test_received_updates_fire_events_without_authored_echo(self):
        import copy,os
        from pathlib import Path
        from unittest import mock
        import persistent_runtime
        path=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(persistent_runtime,'RUST_HOST',path):
            host=persistent_runtime.PersistentLuauHost(copy.deepcopy(transforms.SNAPSHOT))
        self.addCleanup(host.close)
        r=host.call({'op':'execute','source':'''
getgenv().h=game:GetService("Players").LocalPlayer.Character.Humanoid
getgenv().healths={};getgenv().deaths=0
h.HealthChanged:Connect(function(v)table.insert(healths,v)end)
h.Died:Connect(function()deaths+=1 end)
return true
'''})
        self.assertTrue(r['ok'],r)
        r=host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':8,'kind':'set','id':'scope:humanoid-1','fields':{'Health':80}},
            {'sequence':9,'kind':'set','id':'scope:humanoid-1','fields':{'MaxHealth':150}},
            {'sequence':10,'kind':'set','id':'scope:humanoid-1','fields':{'Health':0}},
        ]},'timeout_ms':100})
        self.assertTrue(r['ok'],r);self.assertFalse(r.get('task_errors'),r)
        self.assertFalse([m for m in r.get('mutations',[]) if m.get('property') in ('Health','MaxHealth')])
        r=host.call({'op':'execute','source':'''
assert(#healths==3);assert(healths[1]==80 and healths[2]==80 and healths[3]==0)
assert(deaths==1 and h.Health==0);return true
'''})
        self.assertTrue(r['ok'],r)

if __name__=='__main__':unittest.main()
