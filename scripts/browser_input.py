"""Focused browser keyboard/mouse input on the session owner thread."""
import copy
import json
import math
from pathlib import Path
import re
import secrets
import time

KEY_CODES=json.loads((Path(__file__).resolve().parents[1]/'runtime/web/keyboard-codes.json').read_text())
KEY_NAMES=frozenset(KEY_CODES.values())
MOUSE_BUTTONS=frozenset(('MouseButton1','MouseButton2','MouseButton3'))
MOUSE_TYPES=MOUSE_BUTTONS|{'MouseMovement','MouseWheel'}

def mouse_vector(value,length):
    if (not isinstance(value,list) or len(value)!=length or any(
            type(v) not in (int,float) or not math.isfinite(v) or abs(v)>10_000_000 for v in value)):
        raise ValueError('Mouse coordinates must be bounded finite pixel values')


CLIENT=re.compile(r'[A-Za-z0-9_-]{16,64}\Z')


def validate_keyboard_request(request):
    if not isinstance(request,dict):raise ValueError('Keyboard request must be an object')
    action=request.get('action')
    if action not in ('attach','events','release','heartbeat','detach'):raise ValueError('Invalid keyboard action')
    fields={'action','client_id','sequence'}
    if action!='attach':fields.add('lease')
    if action=='events':fields.add('events')
    if set(request)!=fields:raise ValueError('Invalid keyboard request fields')
    if not isinstance(request['client_id'],str) or not CLIENT.fullmatch(request['client_id']):raise ValueError('Invalid keyboard client')
    if type(request['sequence']) is not int or not 1<=request['sequence']<=2**53-1:raise ValueError('Invalid keyboard sequence')
    if action!='attach' and (not isinstance(request['lease'],str) or not CLIENT.fullmatch(request['lease'])):raise ValueError('Invalid keyboard lease')
    if action=='events':
        events=request['events']
        if not isinstance(events,list) or not 1<=len(events)<=64:raise ValueError('Keyboard batch must contain 1 to 64 events')
        for event in events:
            if not isinstance(event,dict):raise ValueError('Invalid input event')
            if 'input_type' not in event:
                if set(event)!={'phase','key_code'}:raise ValueError('Invalid keyboard event fields')
                if event['phase'] not in ('begin','end','cancel') or not isinstance(event['key_code'],str) or event['key_code'] not in KEY_NAMES:raise ValueError('Invalid keyboard phase or key')
            else:
                if set(event)!={'phase','input_type','position','delta','mouse_location'}:raise ValueError('Invalid mouse event fields')
                if not isinstance(event['input_type'],str) or event['input_type'] not in MOUSE_TYPES:raise ValueError('Invalid mouse input type')
                expected=('begin','end','cancel') if event['input_type'] in MOUSE_BUTTONS else ('change',)
                if event['phase'] not in expected:raise ValueError('Invalid mouse event phase')
                mouse_vector(event['position'],3);mouse_vector(event['delta'],3);mouse_vector(event['mouse_location'],2)
    return copy.deepcopy(request)


class BrowserKeyboard:
    """Keeps key state and heartbeat expiry out of HTTP worker threads.

    A released (unfocused) page may keep its keyboard backend attached with
    heartbeats. A new focused page may replace an unfocused page's lease.
    Expiry/detach restores the previous device profile and cancels held keys.
    """
    def __init__(self,runtime,*,clock=time.monotonic,ttl=2.0):
        if not 1<=ttl<=10:raise ValueError('Keyboard expiry must be 1..10 seconds')
        self.runtime=runtime;self.clock=clock;self.ttl=ttl
        self.client_id=None;self.lease=None;self.focused=False;self.held=set();self.deadline=0
        self.last_sequence=0;self.last_request=None;self.last_result=None
        self.original_keyboard=None;self.original_mouse=None;self.mouse_held=set();self.mouse_location=[0,0]

    def _cancel(self):
        events=[{'phase':'cancel','input_type':'Keyboard','key_code':key,'processed':False} for key in sorted(self.held)]
        events += [{'phase':'cancel','input_type':button,'position':[*self.mouse_location,0],
                    'delta':[0,0,0],'mouse_location':list(self.mouse_location),'processed':False}
                   for button in sorted(self.mouse_held)]
        if not events:return {'ok':True}
        result=self.runtime.deliver_input(events)
        if not result.get('ok'):raise RuntimeError(result.get('error','Input cancellation failed'))
        self.held.clear();self.mouse_held.clear();return result

    def _detach(self):
        if self.client_id is None:return
        # Disabling the attached backend also cancels native held objects when
        # an earlier failed delivery left uncertain state. Preserve a keyboard
        # supplied by another runtime configuration instead of removing it.
        original=self.original_keyboard;original_mouse=self.original_mouse
        try:
            self._cancel()
        finally:
            try:
                result=self.runtime.update_input_capabilities({'KeyboardEnabled':bool(original),'MouseEnabled':bool(original_mouse)})
                if not result.get('ok'):raise RuntimeError(result.get('error','Keyboard detach failed'))
            finally:
                self.client_id=None;self.lease=None;self.focused=False;self.held.clear()
                self.last_sequence=0;self.last_request=None;self.last_result=None;self.original_keyboard=None;self.original_mouse=None;self.mouse_held.clear();self.mouse_location=[0,0]

    def poll(self):
        if self.client_id is not None and self.clock()>=self.deadline:
            try:self._detach()
            except (RuntimeError,ValueError,OSError):pass  # Runtime records errors; expiry must not stop the game tick.

    def close(self):
        try:self._detach()
        except (RuntimeError,ValueError,OSError):pass

    def handle(self,request):
        request=validate_keyboard_request(request);self.poll()
        action=request['action'];client=request['client_id'];sequence=request['sequence']
        if client==self.client_id:
            if sequence==self.last_sequence:
                if request!=self.last_request:raise ValueError('Keyboard sequence was reused for different input')
                self.deadline=self.clock()+self.ttl;return copy.deepcopy(self.last_result)
            if sequence<self.last_sequence:raise ValueError('Keyboard request arrived out of order')
        if action=='attach':
            if self.client_id is not None and client!=self.client_id:
                if self.focused:raise ValueError('Keyboard controls are active in another browser page')
                self._detach()
            if self.client_id is None:
                original=self.runtime.input_capabilities.get('KeyboardEnabled',False)
                original_mouse=self.runtime.input_capabilities.get('MouseEnabled',False)
                result=self.runtime.update_input_capabilities({'KeyboardEnabled':True,'MouseEnabled':True})
                if not result.get('ok'):raise RuntimeError(result.get('error','Keyboard backend is unavailable'))
                self.original_keyboard=original;self.original_mouse=original_mouse;self.client_id=client;self.lease=secrets.token_urlsafe(24)
            self.focused=True
            result={'ok':True,'values':[],'lease':self.lease,'keyboard_attached':True,'mouse_attached':True,'focused':True}
        else:
            if client!=self.client_id or request['lease']!=self.lease:raise ValueError('Keyboard lease expired; focus the controls again')
            if action=='events':
                if not self.focused:raise ValueError('Focus the keyboard controls before sending input')
                held=set(self.held);buttons=set(self.mouse_held);location=list(self.mouse_location);events=[]
                for event in request['events']:
                    is_mouse='input_type' in event
                    if is_mouse:
                        location=list(event['mouse_location'])
                        if event['input_type'] not in MOUSE_BUTTONS:
                            events.append(dict(event,processed=False));continue
                    key=event['input_type'] if is_mouse else event['key_code']
                    active=buttons if is_mouse else held
                    if event['phase']=='begin':
                        if key in active:raise ValueError('Input is already down')
                        active.add(key)
                        if len(held)>64:raise ValueError('Too many held keyboard keys')
                    else:
                        if key not in active:raise ValueError('Input is not down')
                        active.remove(key)
                    events.append(dict(event,processed=False) if is_mouse else dict(event,input_type='Keyboard',processed=False))
                result=self.runtime.deliver_input(events)
                if not result.get('ok'):raise RuntimeError(result.get('error','Input failed'))
                self.held=held;self.mouse_held=buttons;self.mouse_location=location
            elif action=='release':
                self._cancel();self.focused=False;result={'ok':True,'values':[],'focused':False}
            elif action=='detach':
                self._detach();return {'ok':True,'values':[],'keyboard_attached':False}
            else:result={'ok':True,'values':[],'focused':self.focused}
        self.deadline=self.clock()+self.ttl;self.last_sequence=sequence
        self.last_request=request;self.last_result=copy.deepcopy(result)
        return result

# Existing embedders keep their constructor name; new code may use BrowserInput.
BrowserInput = BrowserKeyboard
validate_input_request = validate_keyboard_request
