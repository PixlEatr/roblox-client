-- Pure-library Studio reference; does not modify the DataModel.
local service=game:GetService("TweenService")
local out={version=version(),cases={},trajectory={}}
local function add(name,current,target,velocity,smooth,speed,dt)
 local value,nextVelocity=service:SmoothDamp(current,target,velocity,smooth,speed,dt)
 table.insert(out.cases,{name=name,current={current:GetComponents()},target={target:GetComponents()},
  velocity=typeof(velocity)=="CFrame" and {velocity:GetComponents()} or 0,
  smooth=smooth,speed=speed,dt=dt,value={value:GetComponents()},nextVelocity={nextVelocity:GetComponents()}})
end
local current=CFrame.new(2,3,4)*CFrame.Angles(.3,.7,-.4)
local target=CFrame.new(9,-2,1)*CFrame.Angles(-.2,-1.2,.8)
local velocity=CFrame.new(1,2,3,.2,-.3,.4,5,6,7,8,9,10)
add("stationary identity velocity",CFrame.identity,CFrame.identity,CFrame.identity,.5,10000,.1)
add("translation identity velocity",CFrame.identity,CFrame.new(10,0,0),CFrame.identity,.5,10000,.1)
add("yaw numeric initialization",CFrame.identity,CFrame.Angles(0,1,0),0,.5,10000,.1)
add("yaw identity velocity",CFrame.identity,CFrame.Angles(0,1,0),CFrame.identity,.5,10000,.1)
for _,p in ipairs({{"mixed",.5,10000,.1},{"speed limited",.5,.1,.1},{"zero speed",.5,0,.1},
 {"negative speed",.5,-2,.1},{"instant",0,10000,.1},{"tiny time",.00001,10000,.1},
 {"zero dt",.5,10000,0},{"negative dt",.5,10000,-1}})do
 add(p[1],current,target,velocity,p[2],p[3],p[4])
end
add("near half turn",CFrame.Angles(.2,.1,-.3),CFrame.fromAxisAngle(Vector3.new(1,2,3).Unit,math.pi-.0001),0,.3,10000,1/60)
add("wrapped yaw",CFrame.Angles(0,3.1,0),CFrame.Angles(0,-3.1,0),0,.2,10000,1/60)
add("angular overshoot",CFrame.identity,CFrame.Angles(0,.1,0),CFrame.new(0,0,0,0,100,0,0,0,0,0,0,0),.5,10000,.1)
local value,vel=CFrame.identity,0
for step=1,120 do
 local goal=step<=60 and target or CFrame.new(-4,7,2)*CFrame.Angles(.8,2,-1.2)
 value,vel=service:SmoothDamp(value,goal,vel,.2,30,1/60)
 if step==1 or step==2 or step==10 or step==30 or step==60 or step==61 or step==90 or step==120 then
  table.insert(out.trajectory,{step=step,value={value:GetComponents()},velocity={vel:GetComponents()}})
 end
end
return game:GetService("HttpService"):JSONEncode(out)
