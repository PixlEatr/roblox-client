import copy
import json
from pathlib import Path
import unittest

from local_movement_preview import LocalMovementPreview

SNAPSHOT = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/world_snapshot.json').read_text())


class MovementPreviewTests(unittest.TestCase):
    def preview(self, anchored=False):
        snapshot=copy.deepcopy(SNAPSHOT)
        for node in snapshot['nodes']:
            if node['name']=='HumanoidRootPart':node['properties']['Anchored']=anchored
        return LocalMovementPreview(snapshot), snapshot

    def intent(self, preview, sequence, kind='Move', vector=(1,0,0), relative=False):
        return {'sequence':sequence,'kind':kind,'instance_id':preview.humanoid_id,
                'vector':vector,'relative_to_camera':relative}

    def test_move_preview_keeps_received_snapshot_unchanged(self):
        preview,snapshot=self.preview();before=copy.deepcopy(snapshot)
        initial=preview.report()['predicted_position']
        preview.consume([self.intent(preview,1)])
        result=preview.step(.25)
        self.assertEqual(result['predicted_position'],[initial[0]+4,initial[1],initial[2]])
        self.assertFalse(result['server_confirmed'])
        self.assertEqual(result['network_packets_sent'],0)
        self.assertEqual(snapshot,before)

    def test_move_to_stops_at_target_and_zero_direction_stops(self):
        preview,_=self.preview();initial=preview.report()['predicted_position']
        target=[initial[0]+1,initial[1],initial[2]]
        preview.consume([self.intent(preview,1,'MoveTo',target)])
        self.assertEqual(preview.step(.25)['predicted_position'],target)
        self.assertEqual(preview.step(.25)['predicted_position'],target)
        preview.consume([self.intent(preview,2,vector=(0,0,0))])
        self.assertEqual(preview.step(.25)['predicted_position'],target)

    def test_anchor_camera_and_foreign_receiver_are_explicitly_rejected(self):
        preview,_=self.preview(anchored=True)
        preview.consume([self.intent(preview,1)])
        self.assertEqual(preview.report()['last_reason'],'anchored')
        preview,_=self.preview()
        preview.consume([self.intent(preview,1,relative=True)])
        self.assertEqual(preview.report()['last_reason'],'camera_transform_unavailable')
        intent=self.intent(preview,2);intent['instance_id']='foreign'
        preview.consume([intent])
        self.assertEqual(preview.report()['last_reason'],'different_character')

    def test_duplicate_intents_and_oversized_steps_fail(self):
        preview,_=self.preview();intent=self.intent(preview,1)
        preview.consume([intent])
        with self.assertRaises(ValueError):preview.consume([intent])
        with self.assertRaises(ValueError):preview.step(1)


if __name__=='__main__':unittest.main()
