// Official 585 x 559 classic clothing template. R6 block-body mapping only;
// arbitrary R15/layered meshes require Roblox's avatar texture compositor.
export function clothingFaces(part) {
    const name=part.characterPart,clothing=part.clothing||{},layers=[];
    if(!['Torso','Left Arm','Right Arm','Left Leg','Right Leg'].includes(name))return layers;
    const torso={0:[165,74,64,128],1:[231,8,128,64],2:[427,74,128,128],3:[361,74,64,128],4:[231,204,128,64],5:[231,74,128,128]};
    const right={0:[151,355,64,128],1:[217,289,64,64],2:[85,355,64,128],3:[19,355,64,128],4:[217,485,64,64],5:[217,355,64,128]};
    const left={0:[506,355,64,128],1:[308,289,64,64],2:[440,355,64,128],3:[374,355,64,128],4:[308,485,64,64],5:[308,355,64,128]};
    const add=ref=>{if(ref)for(const [face,crop] of Object.entries(name==='Torso'?torso:name.startsWith('Right')?right:left))layers.push({texture:ref,face:Number(face),crop,color:{$color3:[1,1,1]},clothing:true});};
    if(name==='Torso'){add(clothing.pants);add(clothing.shirt);if(clothing.graphic)layers.push({texture:clothing.graphic,face:5,color:{$color3:[1,1,1]},clothing:true,graphic:true});}
    else add(name.endsWith('Arm')?clothing.shirt:clothing.pants);
    return layers;
}
