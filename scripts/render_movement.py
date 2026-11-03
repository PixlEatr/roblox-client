"""Expiring, sequence-checked movement for one focused local display."""
import math
import re
import time


class SurfaceMovement:
    def __init__(self, execute, *, clock=time.monotonic):
        self.execute=execute
        self.clock=clock
        self.client=None
        self.sequence=0
        self.deadline=0
        self.moving=False

    def _send(self, direction):
        x,_,z=direction
        return self.execute('local p=game:GetService("Players").LocalPlayer '
            'local h=p and p.Character and p.Character:FindFirstChildOfClass("Humanoid") '
            'if h then h:Move(Vector3.new('+repr(x)+',0,'+repr(z)+'),false) end return true',timeout_ms=30)

    def handle(self, request):
        if not isinstance(request,dict) or set(request)!={'client','sequence','direction'}:
            raise ValueError('Invalid movement request')
        client=request['client'];seq=request['sequence'];v=request['direction']
        if (not isinstance(client,str) or not re.fullmatch(r'[a-f0-9]{32}',client)
                or type(seq) is not int or not 1<=seq<2**53
                or not isinstance(v,list) or len(v)!=3 or v[1]!=0
                or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>1 for x in v)):
            raise ValueError('Invalid movement fields')
        self.poll()
        if self.client and self.client!=client and self.moving:
            raise ValueError('Another display controls movement; release its controls first')
        if self.client==client and seq<=self.sequence:
            raise ValueError('Stale movement sequence')
        length=math.hypot(v[0],v[2])
        direction=[v[0]/max(length,1),0,v[2]/max(length,1)]
        result=self._send(direction)
        if not result.get('ok'):return result
        self.client=client;self.sequence=seq;self.deadline=self.clock()+.65
        self.moving=length>0
        return {'ok':True,'values':[]}

    def poll(self):
        if self.moving and self.clock()>=self.deadline:
            self.moving=False
            self._send([0,0,0])

    def close(self):
        if self.moving:
            self.moving=False
            self._send([0,0,0])
