"""Exercise large receipt streams in a real worker, without live accounts."""
import json
from pathlib import Path
import unittest

from persistent_runtime import PersistentLuauHost


class LargeWorldHostTests(unittest.TestCase):
    def test_more_than_twenty_thousand_instances_across_updates(self):
        root = Path(__file__).resolve().parents[1]
        snapshot = json.loads((root/'runtime/tests/world_snapshot.json').read_text())
        with PersistentLuauHost(snapshot) as host:
            sequence = 100
            for first in range(0, 21000, 1000):
                actions = []
                for index in range(first, first+1000):
                    node = {'id': 'large:'+str(index), 'class_name': 'Folder',
                            'name': 'Received'+str(index), 'parent': 'scope:workspace',
                            'properties': {'Archivable': True}}
                    sequence += 1
                    actions.append({'sequence': sequence, 'kind': 'create', 'id': node['id'], 'node': node})
                result = host.call({'op': 'update', 'delta': {'version': 1, 'delta': True,
                                    'sequence': sequence, 'actions': actions}, 'timeout_ms': 20})
                self.assertTrue(result['ok'], result.get('error'))
                self.assertEqual(result['world_revision'], sequence)
            result = host.call({'op': 'execute', 'source': '''
local count=0
for _, child in workspace:GetChildren() do
    if string.sub(child.Name,1,8)=="Received" then count+=1 end
end
return count, workspace:FindFirstChild("Received20999").Archivable
''', 'timeout_ms': 1000})
            self.assertTrue(result['ok'], result.get('error'))
            self.assertEqual(result['values'], [21000, True])
            for first in range(0, 21000, 1000):
                actions=[]
                for index in range(first,first+1000):
                    sequence+=1
                    actions.append({'sequence':sequence,'kind':'remove','id':'large:'+str(index)})
                result=host.call({'op':'update','delta':{'version':1,'delta':True,
                                  'sequence':sequence,'actions':actions},'timeout_ms':20})
                self.assertTrue(result['ok'],result.get('error'))
            result=host.call({'op':'execute','source':'''local count=0
for _, child in workspace:GetChildren() do
    if string.sub(child.Name,1,8)=="Received" then count+=1 end
end
return count
''','timeout_ms':1000})
            self.assertTrue(result['ok'],result.get('error'))
            self.assertEqual(result['values'],[0])

    def test_removal_index_is_invalidated_after_create_and_reparent(self):
        root=Path(__file__).resolve().parents[1]
        snapshot=json.loads((root/'runtime/tests/world_snapshot.json').read_text())
        for name,parent in [('A','scope:workspace'),('B','scope:workspace'),
                            ('D','scope:workspace'),('Child','remove:A')]:
            snapshot['nodes'].append({'id':'remove:'+name,'class_name':'Folder','name':name,
                                      'parent':parent,'properties':{}})
        with PersistentLuauHost(snapshot) as host:
            actions=[{'kind':'remove','id':'remove:D'},
                     {'kind':'set','id':'remove:Child','fields':{'Parent':{'$ref':'remove:B'}}},
                     {'kind':'remove','id':'remove:A'},
                     {'kind':'create','id':'remove:New','node':{'id':'remove:New','class_name':'Folder',
                        'name':'New','parent':'remove:B','properties':{}}}]
            for i,action in enumerate(actions):action['sequence']=100+i
            result=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':103,
                                                   'actions':actions},'timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            result=host.call({'op':'execute','source':'''getgenv().child=workspace.B.Child
getgenv().new=workspace.B.New
return child.Parent==workspace.B,new.Parent==workspace.B
''','timeout_ms':100})
            self.assertEqual(result['values'],[True,True])
            result=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':106,
                'actions':[{'sequence':104,'kind':'remove','id':'remove:Child'},
                    {'sequence':105,'kind':'create','id':'remove:Extra','node':{
                        'id':'remove:Extra','class_name':'Folder','name':'Extra','parent':'remove:B','properties':{}}},
                    {'sequence':106,'kind':'remove','id':'remove:B'}]},'timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            result=host.call({'op':'execute','source':'return child.Parent==nil,new.Parent==nil,workspace:FindFirstChild("B")==nil','timeout_ms':100})
            self.assertEqual(result['values'],[True,True,True])


if __name__ == '__main__':
    unittest.main()
