#!/usr/bin/env python3
"""Offline browser regression fixture: deny WebGL without changing browser settings."""
import copy
import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from script_console import ScriptConsole
from web_console import Handler,WebConsole


class FixtureHandler(Handler):
    def do_GET(self):
        if urlsplit(self.path).path=='/deny-webgl.js':
            code=b'''const original=HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext=function(type,...args){
 if(type==='webgl2'||type==='webgl'||type==='experimental-webgl')return null;
 return original.call(this,type,...args);
};''' if self.console.deny_webgl else b''
            code+=b'''document.addEventListener('DOMContentLoaded',()=>{
 const button=document.createElement('button');button.id='lose-gpu';button.textContent='Simulate GPU loss';
 button.onclick=()=>{const canvas=document.querySelector('.world-stage canvas');const context=canvas?.dataset.renderer==='webgl'?canvas.getContext('webgl2'):null;
 if(context)context.getExtension('WEBGL_lose_context')?.loseContext();else button.textContent='Already using software';};
 document.querySelector('.toolbar').prepend(button);
});'''
            if getattr(self.console,'showcase',False):
                code+=b'''document.addEventListener('DOMContentLoaded',()=>{for(const [label,action] of [['Day / sunset','sunset'],['Toggle pose motion','moving']]){const b=document.createElement('button');b.textContent=label;b.onclick=()=>fetch('/fixture-control?toggle='+action);document.querySelector('.toolbar').prepend(b);}});'''
            return self.reply(200,code,'text/javascript; charset=utf-8')
        if urlsplit(self.path).path=='/fixture-control' and getattr(self.console,'showcase',False):
            from urllib.parse import parse_qs
            key=parse_qs(urlsplit(self.path).query).get('toggle',[''])[0]
            if key in self.console.fixture_state:self.console.fixture_state[key]=not self.console.fixture_state[key]
            return self.reply(200,{'ok':True})
        return super().do_GET()

    def reply(self,status,body,content_type='application/json; charset=utf-8'):
        if isinstance(body,bytes) and content_type.startswith('text/javascript'):
            body=body.replace('● Live · same session'.encode(),'● Offline · no account'.encode())
        if isinstance(body,bytes) and content_type.startswith('text/html'):
            body=body.replace(b'<head>',b'<head><script src="/deny-webgl.js"></script>')
            body=body.replace(b'<title>Rove \xc2\xb7 Live session</title>',b'<title>Rove renderer regression fixture</title>')
            body=body.replace(b'<span id="session-title">Live session</span>',b'<span id="session-title">OFFLINE RENDERER TEST</span>')
        return super().reply(status,body,content_type)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-webgl',action='store_true')
    parser.add_argument('--showcase',action='store_true')
    args=parser.parse_args()
    state={'left':False,'clicks':0}
    def part(id,position,size,color,shape=1):
        return {'id':id,'name':id,'class':'Part','position':{'$vector3':position},'size':{'$vector3':size},'color':{'$color3':color},'shape':shape,'transparency':0}
    frame={'version':1,'parts':[part('Floor',[0,-1,0],[200,1,200],[.45,.65,.4]),
        part('Body',[0,2.2,0],[2,2,1],[.2,.45,.95]),part('Head',[0,3.8,0],[1.2,1.2,1.2],[.95,.75,.45]),
        part('Left leg',[-.55,.7,0],[.9,1.2,.9],[.3,.3,.4]),part('Right leg',[.55,.7,0],[.9,1.2,.9],[.3,.3,.4]),
        part('Ball',[-4,1,0],[2,2,2],[.9,.25,.2],0)],'focus':{'$vector3':[0,2,0]},'players':[{'name':'Offline fixture','local':True}],
        'gui':[{'id':'proof','name':'Proof','class':'TextButton','box':[24,24,260,48,0],'text':'Test UI without WebGL','textSize':16,
        'textColor':{'$color3':[1,1,1]},'background':{'$color3':[.13,.15,.2]},'interactive':True}],
        'meshBounds':0,'skippedParts':0,'skippedGui':0,'terrainUnsupported':False}
    def gui(action):
        state['clicks']+=1;frame['gui'][0]['text']='UI works without WebGL';return {'ok':True,'values':[]}
    render=lambda:copy.deepcopy(frame)
    if args.showcase:
        from renderer_showcase import make_showcase
        frame,render,fetcher,fixture_state=make_showcase()
    with tempfile.TemporaryDirectory(prefix='rove-renderer-',dir='/tmp') as directory:
        socket=ScriptConsole(Path(directory)/'fixture.sock',lambda *a,**kw:{'ok':True,'values':[]},
            render=lambda:{'ok':True,'values':[],'frame':render()},gui_action=gui,
            display=lambda profile:{'ok':True,'values':[]},leave=lambda:state.update(left=True))
        web=WebConsole(socket_path=socket.path);web.server.RequestHandlerClass=FixtureHandler
        web.deny_webgl=not args.allow_webgl
        web.showcase=args.showcase
        if args.showcase:web.render_assets.fetcher=fetcher;web.fixture_state=fixture_state
        connected=web.connected;web.connected=lambda:not state['left'] and connected()
        web.start(open_browser=False)
        print(web.url+'play',flush=True)
        try:
            while True:socket.poll();time.sleep(.002)
        except KeyboardInterrupt:pass
        finally:web.close();socket.close()


if __name__=='__main__':main()
