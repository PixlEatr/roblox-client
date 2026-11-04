"""Public selector behavior through compiled standalone and persistent Luau."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_cframe_transforms as transforms
from persistent_runtime import PersistentLuauHost

ROOT=Path(__file__).resolve().parents[2]
SETUP='''
local root=Instance.new("Folder",workspace);root.Name="QueryRoot"
local group=Instance.new("Model",root);group.Name="Basket"
local apple=Instance.new("Part",group);apple.Name="Red Apple";apple.Anchored=true
apple:AddTag("Fruit");apple:SetAttribute("Flavor",4);apple:SetAttribute("Ripe",false)
local green=Instance.new("WedgePart",group);green.Name="Green";green.Anchored=false
green:SetAttribute("Flavor",5)
local branch=Instance.new("Folder",group);branch.Name="Inner"
local nested=Instance.new("Part",branch);nested.Name="Nested";nested.Anchored=false
local direct=Instance.new("Part",root);direct.Name="Direct";direct.Anchored=false
local tool=Instance.new("Tool",green);tool.Name="Tool"
local text=Instance.new("StringValue",apple);text.Name="Label";text.Value="red"
local function same(actual,expected)
 assert(#actual==#expected,"query count "..#actual.." vs "..#expected)
 local found={}
 for _,item in ipairs(actual)do assert(not found[item],"duplicate result");found[item]=true end
 for _,item in ipairs(expected)do assert(found[item],"missing "..item.Name)end
end
'''


class InstanceQueryTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def test_class_inheritance_names_and_receiver_exclusion(self):
        self.run_script(SETUP+'''
same(root:QueryDescendants("BasePart"),{apple,green,nested,direct})
same(root:QueryDescendants("WedgePart"),{green})
same(root:QueryDescendants("#Direct"),{direct})
same(root:QueryDescendants("[Name = 'Red Apple']"),{apple})
same(root:QueryDescendants("[ClassName = WedgePart]"),{green})
same(root:QueryDescendants("Folder"),{branch})
same(root:QueryDescendants("#QueryRoot"),{})
same(root:QueryDescendants("UnknownClass"),{})
same(root:QueryDescendants("*"),{group,apple,green,branch,nested,direct,tool,text})
''')

    def test_compound_tags_properties_and_attributes_keep_primitive_types(self):
        self.run_script(SETUP+'''
same(root:QueryDescendants("BasePart.Fruit[$Flavor = 4]"),{apple})
same(root:QueryDescendants("BasePart[$Flavor=5]"),{green})
same(root:QueryDescendants("[$Ripe]"),{apple})
same(root:QueryDescendants("[$Ripe = false]"),{apple})
same(root:QueryDescendants("[$Ripe = 'false']"),{})
same(root:QueryDescendants("BasePart[Anchored = false]"),{green,nested,direct})
same(root:QueryDescendants("[Value = red]"),{text})
same(root:QueryDescendants("[$Flavor = '4']"),{})
same(root:QueryDescendants("[$Flavor = 4.0]"),{apple})
same(root:QueryDescendants("[PropertyThatDoesNotExist = 1]"),{})
''')

    def test_direct_descendant_and_leading_combinators(self):
        self.run_script(SETUP+'''
same(root:QueryDescendants("> BasePart"),{direct})
same(root:QueryDescendants(">> BasePart"),{apple,green,nested,direct})
same(root:QueryDescendants("Model > BasePart"),{apple,green})
same(root:QueryDescendants("Model >> BasePart"),{apple,green,nested})
same(root:QueryDescendants("Folder > BasePart"),{nested})
same(root:QueryDescendants("> Model > Folder > Part"),{nested})
same(root:QueryDescendants("Model >> Part > StringValue"),{text})
same(green:QueryDescendants("Model >> Tool"),{})
''')

    def test_comma_union_deduplicates_and_returns_a_fixed_array(self):
        self.run_script(SETUP+'''
local previous=root:QueryDescendants("Part, .Fruit, #Direct, Part")
same(previous,{apple,nested,direct})
direct.Parent=nil
same(previous,{apple,nested,direct})
same(root:QueryDescendants("Part, .Fruit, #Direct"),{apple,nested})
''')

    def test_not_supports_lists_complex_selectors_and_nesting(self):
        self.run_script(SETUP+'''
same(root:QueryDescendants(":not(BasePart, Tool, StringValue)"),{group,branch})
same(root:QueryDescendants("BasePart:not(.Fruit)"),{green,nested,direct})
same(root:QueryDescendants("BasePart:not([$Ripe])"),{green,nested,direct})
same(root:QueryDescendants("BasePart:not(Model > .Fruit)"),{green,nested,direct})
same(root:QueryDescendants("BasePart:not(:not(.Fruit))"),{apple})
''')

    def test_has_uses_relative_scope_and_never_matches_the_receiver(self):
        self.run_script(SETUP+'''
same(root:QueryDescendants(":has(Tool)"),{group,green})
same(root:QueryDescendants(":has(> Tool)"),{green})
same(root:QueryDescendants("BasePart:has(> Tool)"),{green})
same(root:QueryDescendants("Model:has(> BasePart.Fruit)"),{group})
same(root:QueryDescendants("Model:has(> Folder > Part)"),{group})
same(root:QueryDescendants("BasePart:has(> :not(StringValue,Tool))"),{})
same(root:QueryDescendants("BasePart:not(:has(Instance))"),{nested,direct})
same(green:QueryDescendants("BasePart:has(Tool)"),{})
''')

    def test_rename_reparent_retag_attribute_change_and_destroy_are_immediately_visible(self):
        self.run_script(SETUP+'''
same(root:QueryDescendants(".Fruit"),{apple})
apple:RemoveTag("Fruit");direct:AddTag("Fruit")
same(root:QueryDescendants(".Fruit"),{direct})
apple:SetAttribute("Flavor",nil);green:SetAttribute("Flavor",4)
same(root:QueryDescendants("[$Flavor = 4]"),{green})
direct.Name="Changed";same(root:QueryDescendants("#Changed"),{direct})
direct.Parent=branch;same(root:QueryDescendants("Folder > #Changed"),{direct})
direct:Destroy();same(root:QueryDescendants("#Changed, .Fruit"),{})
root.Parent=nil;same(root:QueryDescendants("WedgePart"),{green})
root:Destroy();same(root:QueryDescendants("*"),{})
''')

    def test_quoted_values_unicode_and_script_looking_text_are_data(self):
        self.run_script(SETUP+'''
apple.Name="Pomme-é";same(root:QueryDescendants("#Pomme-é"),{apple})
apple:SetAttribute("Text","red, > [fruit]")
same(root:QueryDescendants([=[[$Text = "red, > [fruit]"]]=]),{apple})
apple.Name='A"B';same(root:QueryDescendants([=[[Name = "A\\"B"]]=]),{apple})
text.Value="error('must not execute')"
same(root:QueryDescendants([=[[Value = "error('must not execute')"]]=]),{text})
apple:SetAttribute("Text","red-blue_4")
same(root:QueryDescendants("[$Text=red-blue_4]"),{apple})
apple:SetAttribute("Text","nan")
same(root:QueryDescendants("[$Text=nan]"),{apple})
apple:SetAttribute("Text","Infinity")
same(root:QueryDescendants("[$Text=Infinity]"),{apple})
''')

    def test_invalid_queries_and_limits_fail_without_mutating_the_tree(self):
        self.run_script(SETUP+'''
local before=#root:GetDescendants()
for _,selector in ipairs({"", " ", ">", ">>> Part", "Part >", "Part,,Model", ":not()",
 "Part + Model", "[Name]", "[$A=nil]", "[A>3]", "[Name='a'", ":unknown(Part)",
 "Part Model", "Part)", "[Name='x'] junk", "Part"..string.char(0),string.rep("x",4097),
 string.rep(":not(",9).."Part"..string.rep(")",9),string.rep("* >> ",65).."*"})do
 assert(not pcall(root.QueryDescendants,root,selector),"should reject "..selector)
end
assert(not pcall(root.QueryDescendants,{},"Part"))
assert(not pcall(root.QueryDescendants,root,12))
assert(not pcall(root.QueryDescendants,root,"Part",true))
same(root.QueryDescendants(green,"> Tool"),{tool})
assert(#root:GetDescendants()==before)
same(root:QueryDescendants("#Direct"),{direct})
''')

    def test_received_tree_queries_do_not_emit_mutations_or_invent_unknown_tag_state(self):
        baseline=self.run_script('')
        results=self.run_script('''
local character=game:GetService("Players").LocalPlayer.Character
local list=workspace:QueryDescendants("Model > Humanoid")
assert(#list==1 and list[1]==character.Humanoid)
assert(not pcall(character.QueryDescendants,character,"BasePart.unknown"))
assert(not pcall(character.QueryDescendants,character,"BasePart[$unknown]"))
''')
        for before,result in zip(baseline,results):self.assertEqual(result.get('mutations'),before.get('mutations'))

    def test_expensive_ancestor_backtracking_is_bounded_and_vm_recovers(self):
        self.run_script('''
local root=Instance.new("Folder")
local parent=root
for _=1,100 do parent=Instance.new("Folder",parent) end
local ok,result=pcall(root.QueryDescendants,root,"MissingClass"..string.rep(" >> *",20))
if ok then assert(#result==0)else assert(string.find(result,"work limit exceeded",1,true),tostring(result))end
assert(#root:QueryDescendants("> Folder")==1)
root:Destroy()
''')

    def test_received_updates_are_seen_by_new_queries_from_existing_handles(self):
        host=PersistentLuauHost(copy.deepcopy(transforms.SNAPSHOT));self.addCleanup(host.close)
        def execute(source):
            result=host.call({'op':'execute','source':source,'timeout_ms':1000})
            self.assertTrue(result['ok'],result);self.assertFalse(result.get('task_errors'),result)
        execute('old=workspace:QueryDescendants("Model > Humanoid");assert(#old==1)')
        result=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':9,'actions':[
            {'sequence':8,'kind':'set','id':'scope:humanoid-1','fields':{'Name':'ChangedHumanoid'}},
            {'sequence':9,'kind':'set','id':'scope:humanoid-1','fields':{'Parent':{'$ref':'scope:workspace'}}}]}})
        self.assertTrue(result['ok'],result)
        execute('''
assert(#workspace:QueryDescendants("Model > Humanoid")==0)
local current=workspace:QueryDescendants("> #ChangedHumanoid")
assert(#current==1 and current[1]==old[1])
''')

    def test_unresolved_parent_is_not_treated_as_a_descendant(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        next(n for n in snapshot['nodes'] if n['class_name']=='Model')['parent']='scope:unresolved'
        self.run_script('assert(#workspace:QueryDescendants("Humanoid, BasePart")==0)',snapshot)

    def test_demo_runs_in_both_workers(self):
        with tempfile.TemporaryDirectory(prefix='instance-query-demo-') as directory:
            path=Path(directory)/'snapshot.json';path.write_text(json.dumps(transforms.SNAPSHOT))
            for host in transforms.HOSTS:
                with self.subTest(host=host.name):
                    result=subprocess.run([str(host),'--source',str(ROOT/'runtime/scripts/instance_queries_demo.luau'),
                                           '--world-snapshot',str(path)],capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    body=json.loads(result.stdout);self.assertTrue(body['ok'],body)
                    self.assertEqual(body['values'],['Instance queries completed',1])


if __name__=='__main__':unittest.main()
