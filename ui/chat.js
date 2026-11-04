window.createChatControls=({input,button,form,channel,available=()=>true,submit,onResult=()=>{},onError=()=>{}})=>{
    let typed=null,busy=false;
    input.addEventListener('input',event=>{typed=event.isTrusted?input.value:null;});
    const send=async event=>{
        event.preventDefault();
        if(!event.isTrusted||event.isComposing||busy||!available()||typed!==input.value||!typed?.trim())return;
        if([...typed].length>200){onError('Chat messages can contain up to 200 characters.');return;}
        const text=typed;typed=null;busy=true;button.disabled=true;
        try{const result=await submit({text,channel:channel?.value||'RBXGeneral'});input.value='';onResult(result);}
        catch(error){onError(error.message);}
        finally{busy=false;button.disabled=false;}
    };
    form.addEventListener('submit',event=>event.preventDefault());
    button.addEventListener('click',send);
    input.addEventListener('keydown',event=>{if(event.key==='Enter')return send(event);});
};
