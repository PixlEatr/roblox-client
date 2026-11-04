"""Behavioral integration checks, separate from the unchanged UNC smoke suite."""
import base64
import hashlib
import json
import socketserver
import struct
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_local_compat as compat


class IntegrationTests(unittest.TestCase):
    run_script = compat.LocalCompatibilityTests.run_script

    def test_hidden_properties_remain_coherent_and_overrides_are_per_instance(self):
        self.run_script('''
local a,b=Instance.new("Fire"),Instance.new("Fire")
assert(not pcall(function()return a.size_xml end))
assert(sethiddenproperty(a,"size_xml",7));assert(a.Size==7)
local value,hidden=gethiddenproperty(a,"size_xml");assert(value==7 and hidden)
assert(setscriptable(a,"size_xml",true)==false)
a.size_xml=9;assert(a.Size==9 and a.size_xml==9)
assert(not isscriptable(b,"size_xml"))
assert(setscriptable(a,"Size",false));assert(not pcall(function()return a.Size end))
assert(gethiddenproperty(a,"Size")==9 and not isscriptable(a,"Size"))
sethiddenproperty(a,"Size",4);assert(gethiddenproperty(a,"size_xml")==4)
assert(not pcall(gethiddenproperty,a,"nonexistent"))
''')

    def test_real_module_cache_environments_source_and_bytecode(self):
        self.run_script('''
local module=Instance.new("ModuleScript")
assert(not pcall(getsenv,module))
module.Source="privateValue=17;_G.moduleSentinel=23;shared.loads=(shared.loads or 0)+1;return {value=privateValue,script=script}"
local bytes=getscriptbytecode(module)
assert(#bytes>0 and getscripthash(module)==crypt.hash(bytes,"sha256"))
local first=require(module);assert(first.value==17 and first.script==module)
assert(require(module)==first and shared.loads==1)
assert(require(cloneref(module))==first and shared.loads==1)
assert(getsenv(module).privateValue==17 and privateValue==nil)
assert(getsenv(cloneref(module))==getsenv(module))
assert(getrenv()~=getgenv() and getsenv(module).shared==getrenv().shared)
assert(getrenv()._G~=_G and getrenv()._G.moduleSentinel==23 and _G.moduleSentinel==nil)
assert(table.find(getloadedmodules(),module) and table.find(getscripts(),module))
local closure=getscriptclosure(module);local fresh=closure()
assert(fresh~=first and fresh.value==17 and shared.loads==2)
local copy=module:Clone();assert(copy~=module and getscriptbytecode(copy)==bytes)
copy.Source="return {value=99}"
assert(getscriptbytecode(copy)~=bytes and getscripthash(copy)~=getscripthash(module))
assert(require(copy).value==99 and require(module)==first)
copy.Archivable=false;assert(copy:Clone()==nil)
''')

    def test_click_detector_delivers_requested_event_and_player(self):
        self.run_script('''
local detector=Instance.new("ClickDetector")
local got,other=0,0
detector.MouseClick:Connect(function(player)
 assert(player==game:GetService("Players").LocalPlayer);got+=1
end)
detector.RightMouseClick:Connect(function()other+=1 end)
fireclickdetector(detector,2);task.wait();assert(got==1 and other==0)
fireclickdetector(detector,0,"RightMouseClick");task.wait();assert(got==1 and other==1)
assert(not pcall(fireclickdetector,detector,-1))
detector:Destroy();assert(not pcall(fireclickdetector,detector))
''')

    def test_frame_events_measure_elapsed_time(self):
        self.run_script('''
setfpscap(30)
local started=os.clock();local sum=0
for i=1,3 do
 local dt=game:GetService("RunService").RenderStepped:Wait()
 assert(dt>0 and dt<1);sum+=dt
end
local elapsed=os.clock()-started
assert(elapsed>=0.05 and math.abs(elapsed-sum)<0.08)
assert(not pcall(setfpscap,-1));assert(not pcall(setfpscap,math.huge))
''')

    def test_svg_exports_geometry_text_and_actual_asset_data(self):
        results=self.run_script('''
local square=Drawing.new("Square");square.Visible=true;square.Filled=true
square.Position=Vector2.new(10,20);square.Size=Vector2.new(30,40)
square.Color=Color3.fromRGB(255,0,0);square.Transparency=0.5
local text=Drawing.new("Text");text.Visible=true;text.Text='<a & "b">';text.ZIndex=2
local image=Drawing.new("Image");image.Visible=true;image.Size=Vector2.new(1,1)
local png=crypt.base64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
writefile("pixel.png",png);image.Data=getcustomasset("pixel.png")
assert(getcustomassetdata(image.Data)==png)
print(Drawing.ToSVG(100,100))
square:Destroy();assert(not isrenderobj(square))
assert(not pcall(setrenderproperty,square,"Visible",true))
cleardrawcache();assert(not isrenderobj(text))
assert(not Drawing.ToSVG():find("<rect",1,true))
assert(not pcall(Drawing.new,"unsupported"))
''')
        root=ET.fromstring(results[0]['output'].strip())
        ns={'s':'http://www.w3.org/2000/svg'}
        rect=root.find('s:rect',ns)
        self.assertEqual((rect.get('x'),rect.get('y'),rect.get('width'),rect.get('height')),('10','20','30','40'))
        self.assertEqual(rect.get('fill'),'rgb(255,0,0)')
        self.assertEqual(rect.get('opacity'),'0.5')
        self.assertEqual(root.find('s:text',ns).text,'<a & "b">')
        image=root.find('s:image',ns)
        self.assertTrue(base64.b64decode(image.get('href').split(',')[1]).startswith(b'\x89PNG\r\n\x1a\n'))

    def test_font_enum_noncontiguous_values(self):
        self.run_script('''
assert(Enum.Font.Unknown.Value==100 and Enum.Font:FromValue(100)==Enum.Font.Unknown)
assert(Enum.Font:FromValue(52)==nil and Enum.Font:FromValue(99)==nil)
assert(Enum.Font.SourceSans.Value==3 and #Enum.Font:GetEnumItems()==53)
assert(Enum.MessageType.MessageError.Value==3)
assert(Enum.Font.Arial~=Enum.MessageType.MessageInfo)
''')


class NetworkTests(unittest.TestCase):
    run_script = compat.LocalCompatibilityTests.run_script

    def serve(self,server):
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: (server.shutdown(),thread.join(3)))
        return server.server_address[1]

    def test_httpget_aliases_body_errors_and_loadstring(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Cookie'))
                self.send_response(404 if self.path=='/missing' else 200)
                self.end_headers();self.wfile.write(b'return 42')
            def log_message(self,*args):pass
        port=self.serve(ThreadingHTTPServer(('127.0.0.1',0),Handler))
        self.run_script(f'''
local url="http://127.0.0.1:{port}"
assert(game:HttpGet(url)=="return 42")
assert(game:HttpGetAsync(url,true)=="return 42")
assert(httpget(url,false)=="return 42")
assert(loadstring(game:HttpGet(url))()==42)
assert(not pcall(function()game:HttpGet(url.."/missing")end))
assert(not pcall(function()game:HttpGet("file:///tmp/no")end))
''')
        self.assertEqual(seen,[None]*5)

    def test_http_binary_post_status_headers_and_size_error(self):
        seen=[]
        larger_body=b'large-response-'*12000
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length',0)))
                seen.append((body,self.headers.get('X-Test'),self.headers.get('Cookie')))
                self.send_response(201);self.send_header('X-Reply','actual');self.end_headers();self.wfile.write(body)
            def do_GET(self):
                if self.path=='/absent':
                    self.send_response(404);body=b'absent'
                elif self.path=='/oversized':
                    self.send_response(200);body=b''
                else:
                    self.send_response(200);body=larger_body
                content_length=16*1024*1024+1 if self.path=='/oversized' else len(body)
                self.send_header('Content-Length',str(content_length));self.end_headers()
                self.wfile.write(body)
            def log_message(self,*args):pass
        port=self.serve(ThreadingHTTPServer(('127.0.0.1',0),Handler))
        self.run_script(f'''
local url="http://127.0.0.1:{port}"
local data=string.char(0,255).."payload"
local r=request({{Url=url,Method="POST",Headers={{["X-Test"]="sent"}},Body=data}})
assert(r.Success and r.StatusCode==201 and r.Body==data)
assert(r.Headers["x-reply"]=="actual")
r=request({{Url=url.."/absent"}});assert(not r.Success and r.StatusCode==404 and r.Body=="absent")
local large=request({{Url=url.."/larger"}})
assert(large.Success and #large.Body=={len(larger_body)} and large.Body:sub(1,15)=="large-response-")
assert(not pcall(request,{{Url=url.."/oversized"}}))
assert(not pcall(request,{{Url="file:///tmp/no"}}))
''')
        self.assertEqual(seen,[(b'\x00\xffpayload','sent',None)])

    def test_websocket_real_masked_binary_echo_and_close(self):
        received=[];errors=[];finished=threading.Event()
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    self.connection.settimeout(3)
                    self.rfile.readline();headers={}
                    while True:
                        line=self.rfile.readline()
                        if line==b'\r\n':break
                        key,value=line.decode().split(':',1);headers[key.lower()]=value.strip()
                    accept=base64.b64encode(hashlib.sha1((headers['sec-websocket-key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
                    self.wfile.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n');self.wfile.flush()
                    def read_frame():
                        head=self.rfile.read(2)
                        if len(head)!=2:raise ValueError('missing frame')
                        size=head[1]&127
                        if size==126:size=struct.unpack('!H',self.rfile.read(2))[0]
                        elif size==127:size=struct.unpack('!Q',self.rfile.read(8))[0]
                        if size>131072 or not head[1]&128:raise ValueError('invalid client frame')
                        mask=self.rfile.read(4);body=self.rfile.read(size)
                        return head[0]&15,bytes(c^mask[i%4] for i,c in enumerate(body))
                    opcode,body=read_frame();received.append((opcode,body))
                    self.wfile.write(bytes([0x82,len(body)])+body);self.wfile.flush()
                    opcode,body=read_frame();received.append((opcode,body))
                except Exception as error:errors.append(str(error))
                finally:finished.set()
        class Server(socketserver.ThreadingTCPServer):daemon_threads=True
        port=self.serve(Server(('127.0.0.1',0),Handler))
        self.run_script(f'''
local socket=WebSocket.connect("ws://127.0.0.1:{port}/echo")
local got,closed
socket.OnMessage:Connect(function(data)got=data end)
socket.OnClose:Connect(function()closed=true end)
local data=string.char(0,255).."echo"
socket:Send(data,true)
for i=1,100 do if got then break end;task.wait(.005) end
assert(got==data)
socket:Close();task.wait();assert(closed)
assert(not pcall(socket.Send,socket,"after close"))
''')
        self.assertTrue(finished.wait(3),'server did not receive the close frame')
        self.assertFalse(errors)
        self.assertEqual(received[0],(2,b'\x00\xffecho'))
        self.assertEqual(received[1][0],8)


if __name__=='__main__':unittest.main()
