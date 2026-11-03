"""Collision response must update the momentum used by subsequent steps."""
import math
import copy
import unittest

from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, OBB, GroundGeometry
from test_body_velocity import world


def controller(*colliders):
    data=world();root=data['nodes'][-1]
    root['properties']['Size']={'$vector3':[1,1,1]};root['origins']['Size']='received'
    return OwnCharacterPhysicsController(data,geometry=GroundGeometry(tuple(colliders),()),gravity=0)


def impulse(c,vector,sequence=1):
    c.consume([{'sequence':sequence,'kind':'Impulse','instance_id':'root',
                'vector':[2*x for x in vector]}])


WALL=AABB((3,-100,-100),(3.1,100,100),'wall')


class ContactVelocityTests(unittest.TestCase):
    def test_wall_clears_stored_normal_momentum(self):
        c=controller(WALL);impulse(c,(50,0,0));c.step(.2)
        self.assertEqual(c.linear_velocity[0],0)
        self.assertAlmostEqual(c._impulse_velocity[0],0)
        x=c.controller.position[0]
        c.update_geometry(GroundGeometry((),()));c.step(.1)
        self.assertAlmostEqual(c.controller.position[0],x)

    def test_tangent_momentum_survives_wall_contact(self):
        c=controller(WALL);impulse(c,(50,0,10));c.step(.2)
        self.assertAlmostEqual(c._impulse_velocity[0],0)
        self.assertAlmostEqual(c._impulse_velocity[2],10)
        x,z=c.controller.position[0],c.controller.position[2]
        c.update_geometry(GroundGeometry((),()));c.step(.1)
        self.assertAlmostEqual(c.controller.position[0],x)
        self.assertAlmostEqual(c.controller.position[2],z+1)

    def test_walk_away_after_knockback_does_not_fight_old_momentum(self):
        c=controller(WALL);impulse(c,(50,0,0));c.step(.2)
        x=c.controller.position[0]
        c.consume([{'sequence':2,'kind':'Move','instance_id':'humanoid','vector':[-1,0,0]}])
        c.step(.1)
        self.assertAlmostEqual(c.controller.position[0],x-1)
        self.assertAlmostEqual(c.linear_velocity[0],-10)

    def test_corner_removes_both_inward_components(self):
        second=AABB((-100,-100,3),(100,100,3.1),'second-wall')
        c=controller(WALL,second);impulse(c,(50,0,50));c.step(.2)
        self.assertEqual(c._impulse_velocity,(0,0,0))

    def test_impulse_without_contact_retains_momentum(self):
        c=controller();impulse(c,(50,0,10));c.step(.2);c.step(.1)
        self.assertEqual(c._impulse_velocity,(50,0,10))
        self.assertAlmostEqual(c.controller.position[0],15)

    def test_moving_away_from_wall_keeps_away_velocity(self):
        c=controller(WALL);c.controller.resolve_position((2.5,20,0))
        impulse(c,(-10,0,3));c.step(.1)
        self.assertEqual(c._impulse_velocity,(-10,0,3))
        self.assertAlmostEqual(c.controller.position[0],1.5)

    def test_angled_wall_keeps_only_tangent_momentum(self):
        q=math.sqrt(.5)
        wall=OBB((3,20,0),(.05,100,100),(q,0,q,0,1,0,-q,0,q),'angled-wall')
        c=controller(wall);impulse(c,(50,0,0));c.step(.2)
        self.assertAlmostEqual(c._impulse_velocity[0],25)
        self.assertAlmostEqual(c._impulse_velocity[2],25)
        self.assertLess(math.hypot(*c._impulse_velocity),50)

    def test_conveyor_inheritance_cannot_restore_blocked_momentum(self):
        belt=AABB((-.25,16,-10),(.25,17,10),'belt')
        c=controller(WALL,belt);c.grounded=True;c._ground_source='belt'
        c.update_surface_velocities({'belt':(20,0,0)})
        c.step(.2)
        self.assertFalse(c.grounded)
        self.assertAlmostEqual(c._impulse_velocity[0],0)
        x=c.controller.position[0]
        c.update_geometry(GroundGeometry((belt,),()));c.step(.1)
        self.assertAlmostEqual(c.controller.position[0],x)

    def test_removing_force_after_contact_does_not_release_stored_push(self):
        c=controller(WALL)
        c.update_force_sources([{'id':'force','source':'received_force','kind':'BodyForce',
            'relative_to':'World','part_id':'root','force':(1000,0,0)}])
        c.step(.2);self.assertAlmostEqual(c._impulse_velocity[0],0)
        x=c.controller.position[0]
        c.update_force_sources([]);c.update_geometry(GroundGeometry((),()));c.step(.1)
        self.assertAlmostEqual(c.controller.position[0],x)


class NativeContactVelocityTests(unittest.TestCase):
    def test_real_luau_impulse_stops_at_wall_without_later_lurch(self):
        import test_cframe_transforms as transforms
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        root['properties'].update(AssemblyMass=2,Anchored=False,Size={'$vector3':[1,1,1]})
        root['properties']['CFrame']['$cframe']['position']=[0,20,0]
        root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',Size='received',CFrame='received')
        original=copy.deepcopy(data)
        results=transforms.CFrameTransformTests.run_script(self,'''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root:ApplyImpulse(Vector3.new(100,0,20))
''',data)
        for result in results:
            c=OwnCharacterPhysicsController(data,geometry=GroundGeometry((WALL,),()),gravity=0)
            c.consume(result['movement_intents']);c.step(.2)
            self.assertAlmostEqual(c._impulse_velocity[0],0)
            self.assertAlmostEqual(c._impulse_velocity[2],10)
            x=c.controller.position[0]
            c.update_geometry(GroundGeometry((),()));c.step(.1)
            self.assertAlmostEqual(c.controller.position[0],x)
        self.assertEqual(data,original)


if __name__=='__main__':unittest.main()
