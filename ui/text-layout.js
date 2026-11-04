/* Display layout measured with browser glyph shaping and the loaded FontFace. */
window.RoveText=(()=>{
    let measureElement=null;
    const graphemes=new Intl.Segmenter(undefined,{granularity:'grapheme'});
    const words=new Intl.Segmenter(undefined,{granularity:'word'});
    const finite=(n,fallback)=>typeof n==='number'&&Number.isFinite(n)?n:fallback;
    const clamp=(n,low,high)=>Math.max(low,Math.min(high,n));
    const isText=g=>['TextLabel','TextButton','TextBox'].includes(g.class);
    function strokeColor(stroke){
        const color=stroke.color?.$color3;
        const rgb=(Array.isArray(color)&&color.length===3?color:[0,0,0]).map(value=>Math.round(clamp(finite(value,0),0,1)*255));
        return 'rgba('+rgb.join(',')+','+(1-clamp(finite(stroke.transparency,0),0,1))+')';
    }
    function applyTextInk(entry,g){
        const {element:e}=entry,outline=g.textOutline,legacy=g.legacyTextStroke;
        if(outline){
            const thickness=clamp(finite(outline.thickness,0),0,1000);
            e.style.setProperty('--glyph-stroke-width',outline.scaled?'min(1000px, '+thickness+'em)':thickness+'px');
            e.style.setProperty('--glyph-stroke-color',strokeColor(outline));
        }else if(entry.hasTextOutline){
            e.style.removeProperty('--glyph-stroke-width');
            e.style.removeProperty('--glyph-stroke-color');
        }
        entry.hasTextOutline=!!outline;
        if(legacy){
            const color=strokeColor(legacy);
            e.style.textShadow=['1px 0','-1px 0','0 1px','0 -1px'].map(offset=>offset+' '+color).join(', ');
        }else e.style.textShadow='';
    }
    // Textarea indices are UTF-16 and the value collapses CRLF to LF.
    function editorOffset(source,offset,toBytes){
        let units=0,bytes=0;
        for(let i=0;i<source.length;){
            const point=source.codePointAt(i);
            const crlf=point===13&&source.charCodeAt(i+1)===10;
            const width=point>0xffff?2:1;
            const size=crlf?2:point<0x80?1:point<0x800?2:point<=0xffff?3:4;
            if((toBytes?units+width:bytes+size)>offset)break;
            units+=width;bytes+=size;i+=crlf?2:width;
        }
        return toBytes?bytes:units;
    }
    function measurer(){
        if(!measureElement){
            measureElement=document.createElement('div');measureElement.setAttribute('aria-hidden','true');
            Object.assign(measureElement.style,{position:'fixed',left:'-100000px',top:'0',visibility:'hidden',pointerEvents:'none',display:'inline-block',margin:'0',padding:'0',border:'0',boxSizing:'content-box',height:'auto',minHeight:'0',maxHeight:'none',contain:'layout style'});
            document.body.append(measureElement);
        }
        return measureElement;
    }
    // Callers may inject a measurer for deterministic search tests. The live
    // display uses the browser, never character-count estimates.
    function fitSize(measure,minimum,maximum,width,height){
        let low=Math.ceil(minimum),high=Math.floor(maximum),best=low;
        while(low<=high){const middle=Math.floor((low+high)/2),box=measure(middle);
            if(box.width<=width+.25&&box.height<=height+.25){best=middle;low=middle+1;}else high=middle-1;
        }
        return best;
    }
    function visibleEnd(entry,text,limit){
        if(limit<0||limit>=text.length)return Infinity;
        if(entry.graphemeText!==text){
            entry.graphemeOffsets=Array.from(graphemes.segment(text),part=>part.index);
            entry.graphemeText=text;
        }
        return entry.graphemeOffsets[limit]??Infinity;
    }
    function truncateText(text,runs,mode,fits){
        const ends=Array.from(graphemes.segment(text),part=>part.index);
        function candidate(end){
            const prefix=text.slice(0,end).trimEnd();
            const clipped=[];
            let remaining=prefix.length;
            for(const run of runs||[{text,style:{}}]){
                if(!remaining)break;
                clipped.push({...run,text:run.text.slice(0,remaining)});
                remaining-=Math.min(remaining,run.text.length);
            }
            clipped.push({...clipped.at(-1),style:clipped.at(-1)?.style||{},text:'...',marker:true});
            return {text:prefix+'...',runs:clipped,cut:prefix.length};
        }
        function longest(ends){
            let low=0,high=ends.length-1,best=null;
            while(low<=high){
                const middle=Math.floor((low+high)/2),choice=candidate(ends[middle]);
                if(fits(choice)){best=choice;low=middle+1;}
                else high=middle-1;
            }
            return best;
        }
        if(mode===1){
            const boundaries=new Set(ends),wordEnds=[];
            for(const part of words.segment(text)){
                const end=part.index+part.segment.length;
                if(boundaries.has(end)&&part.segment.trim())wordEnds.push(end);
            }
            const word=longest(wordEnds);
            if(word)return word;
        }
        return longest(ends)||{text:'',runs:null,cut:0};
    }
    function setContent(element,text,runs,baseSize,g,fonts,end=Infinity){
        if(!runs&&end===Infinity){element.textContent=text;return [];}
        element.replaceChildren();
        const runFonts=[];
        let offset=0;
        const color=(rgb,alpha)=>'rgba('+rgb.join(',')+','+alpha+')';
        const baseColor=g.textColor?.$color3;
        const baseAlpha=1-clamp(finite(g.textTransparency,0),0,1);
        for(const run of runs||[{text,style:{}}]){
            const style=run.style;
            const span=document.createElement('span');
            if(run.marker)span.style.whiteSpace='nowrap';
            const value=run.text.replace(/\t/g,' ');
            if(run.fontFace&&fonts){fonts.apply(span,run.fontFace);runFonts.push([span,run.fontFace]);}
            if(style.weight)span.style.fontWeight=String(style.weight);
            if(style.style!==undefined)span.style.fontStyle=style.style===1?'italic':'normal';
            if(style.size||style.sizeScale)span.style.fontSize=((style.size||baseSize)/baseSize*(style.sizeScale||1))+'em';
            const decorations=[];
            if(style.underline)decorations.push('underline');
            if(style.strike)decorations.push('line-through');
            if(decorations.length)span.style.textDecoration=decorations.join(' ');
            if(style.color||style.transparency!==undefined){
                const rgb=style.color||(baseColor||[0,0,0]).map(x=>Math.round(clamp(x,0,1)*255));
                span.style.color=color(rgb,baseAlpha*(1-(style.transparency||0)));
            }
            if(style.mark)span.style.backgroundColor=color(style.mark,1-(style.markTransparency||0));
            if(style.stroke){
                const {color:rgb,thickness,transparency,sizing}=style.stroke;
                span.style.webkitTextStrokeWidth=thickness+(sizing==='scaled'?'em':'px');
                span.style.webkitTextStrokeColor=color(rgb,1-transparency);
                span.style.paintOrder='stroke fill';
            }
            const count=clamp(end-offset,0,value.length);
            span.textContent=value;
            if(count===0)span.style.visibility='hidden';
            element.append(span);
            if(count>0&&count<value.length){
                // Leave the suffix in flow with the same style so line breaks
                // and alignment still account for the whole string.
                const suffix=span.cloneNode(false);
                span.textContent=value.slice(0,count);
                suffix.textContent=value.slice(count);
                suffix.style.visibility='hidden';
                element.append(suffix);
                if(run.fontFace&&fonts)runFonts.push([suffix,run.fontFace]);
            }
            offset+=value.length;
        }
        return runFonts;
    }
    function updateTextBoxPreview(entry,g,fonts,fontGeneration,placeholder,limit){
        const e=entry.element,styled=!placeholder&&Array.isArray(g.richRuns);
        const showPreview=styled||limit>=0||g.textTruncate===1||g.textTruncate===2;
        e.classList.toggle('text-preview-editor',showPreview);
        if(!showPreview){
            entry.richPreview?.element.remove();
            entry.richPreview=null;
            return;
        }
        if(!entry.richPreview){
            const preview=document.createElement('div');
            preview.className='game-node text-box-preview';
            preview.setAttribute('aria-hidden','true');
            e.after(preview);
            entry.richPreview={element:preview};
        }
        const preview=entry.richPreview.element;
        for(const property of ['left','top','width','height','transform','zIndex','clipPath','visibility','borderRadius','color','fontFamily','fontWeight','fontStyle'])
            preview.style[property]=e.style[property];
        const frame={...g,class:'TextLabel'};
        if(placeholder){
            frame.text=placeholder;
            frame.richRuns=null;
            preview.style.color=e.style.getPropertyValue('--placeholder-color');
        }
        apply(entry.richPreview,frame,fonts,fontGeneration);
    }
    function apply(entry,g,fonts,fontGeneration=0){
        const e=entry.element;if(!isText(g))return;
        applyTextInk(entry,g);
        const editable=e.tagName==='TEXTAREA'||e.tagName==='INPUT';
        const [, ,rawWidth,rawHeight]=g.box;
        const width=Math.max(0,finite(rawWidth,0)),height=Math.max(0,finite(rawHeight,0));
        const scale=clamp(finite(g.textScale,1),.001,1000),lineHeight=clamp(finite(g.lineHeight,1),1,3);
        const scaled=g.textScaled===true,wrapped=scaled||g.textWrapped===true;
        const alignment=[0,1,2].includes(g.textXAlignment)?g.textXAlignment:2;
        const vertical=[0,1,2].includes(g.textYAlignment)?g.textYAlignment:1;
        const direction=['auto','ltr','rtl'][g.textDirection]||'auto';
        const text=String(g.text||'').replace(/\t/g,' ');
        const placeholder=editable&&!text?String(g.placeholderText||'').replace(/\t/g,' '):'';
        const displayText=placeholder||text;
        const limit=Math.trunc(finite(g.maxVisibleGraphemes,-1));
        const runs=!editable&&Array.isArray(g.richRuns)?g.richRuns:null;
        const truncate=!editable&&[1,2].includes(g.textTruncate)?g.textTruncate:0;
        const fullText=runs?String(g.contentText??text):displayText;
        const baseSize=clamp(finite(g.textSize,14),1,1000);
        let minimum=1,maximum=scaled?100:1000;
        if(Array.isArray(g.textSizeConstraint)&&g.textSizeConstraint.length===2){minimum=clamp(finite(g.textSizeConstraint[0],1),1,1000);maximum=clamp(finite(g.textSizeConstraint[1],100),minimum,1000);}
        e.dir=direction;e.style.textAlign=['left','right','center'][alignment];e.style.lineHeight=String(lineHeight);
        const signature=JSON.stringify([displayText,width,height,scale,lineHeight,scaled,wrapped,minimum,maximum,g.textSize,e.style.fontFamily,e.style.fontWeight,e.style.fontStyle,direction,runs,runs?fontGeneration:0,truncate]);
        if(entry.textSignature!==signature){
            const probe=measurer();setContent(probe,displayText,runs,baseSize,g,fonts);probe.dir=direction;
            Object.assign(probe.style,{fontFamily:e.style.fontFamily,fontWeight:e.style.fontWeight,fontStyle:e.style.fontStyle,lineHeight:String(lineHeight),whiteSpace:wrapped?'pre-wrap':'pre',overflowWrap:wrapped?'anywhere':'normal',wordBreak:'normal',width:wrapped?width+'px':'max-content'});
            const measure=size=>{
                if(!fullText)return {width:0,height:0};
                probe.style.fontSize=size*scale+'px';const rect=probe.getBoundingClientRect();
                // scrollWidth/Height are integer pixels while layout boxes
                // can be fractional. Their ordinary ceil must not make a
                // perfectly fitting wrapped line look like an overflow.
                return {width:Math.max(rect.width,probe.scrollWidth>Math.ceil(rect.width)?probe.scrollWidth:0),height:Math.max(rect.height,probe.scrollHeight>Math.ceil(rect.height)?probe.scrollHeight:0)};
            };
            const size=scaled?fitSize(measure,minimum,maximum,width,height):clamp(finite(g.textSize,14),minimum,maximum);
            const box=measure(size);
            let shortened=null;
            if(truncate&&(box.width>width+.25||box.height>height+.25)){
                shortened=truncateText(fullText,runs,truncate,choice=>{
                    setContent(probe,choice.text,choice.runs,baseSize,g,fonts);
                    const bounds=measure(size);
                    return bounds.width<=width+.25&&bounds.height<=height+.25;
                });
            }
            entry.textLayout={size:size*scale,width:box.width,height:box.height,line:size*scale*lineHeight,shortened};
            entry.textSignature=signature;
        }
        const layout=entry.textLayout;e.style.fontSize=layout.size+'px';
        e.dataset.textSize=String(layout.size);e.dataset.textFits=String(layout.width<=width+.25&&layout.height<=height+.25);
        if(editable){
            e.style.resize='none';e.style.whiteSpace=wrapped?'pre-wrap':'pre';e.style.overflowWrap=wrapped?'anywhere':'normal';
            e.style.paddingTop=(vertical===0?0:Math.max(0,height-Math.min(height,layout.height))*(vertical===1?.5:1))+'px';
            e.style.paddingBottom='0';e.wrap=wrapped?'soft':'off';
            e.readOnly=g.textEditable===false;entry.multiLine=g.multiLine===true;
            e.placeholder=String(g.placeholderText||'');
            if(document.activeElement!==e&&e.value!==text)e.value=text;
            updateTextBoxPreview(entry,g,fonts,fontGeneration,placeholder,limit);
            return;
        }
        e.style.display='flex';e.style.flexDirection='column';e.style.alignItems='stretch';e.style.justifyContent=['flex-start','center','flex-end'][vertical];
        if(!entry.textElement||entry.textElement.parentElement!==e){
            entry.textElement=document.createElement('span');e.replaceChildren(entry.textElement);
        }
        const content=entry.textElement;
        const maxHeight=runs?height:Math.max(0,Math.floor((height+.001)/layout.line)*layout.line);
        Object.assign(content.style,{display:'block',width:'100%',flexShrink:'0',whiteSpace:wrapped?'pre-wrap':'pre',overflowWrap:wrapped?'anywhere':'normal',wordBreak:'normal',overflow:'hidden',maxHeight:wrapped?maxHeight+'px':'none'});
        const shortened=layout.shortened;
        const revealEnd=visibleEnd(entry,fullText,limit);
        const end=shortened&&revealEnd>shortened.cut?Infinity:revealEnd;
        const contentSignature=JSON.stringify([text,runs,baseSize,runs?fontGeneration:0,runs?g.textColor:null,runs?g.textTransparency:null,end,shortened?.text]);
        if(entry.contentSignature!==contentSignature||entry.contentTarget!==content){
            entry.runFonts=setContent(content,shortened?shortened.text:text,shortened?shortened.runs:runs,baseSize,g,fonts,end);
            entry.contentSignature=contentSignature;
            entry.contentTarget=content;
        }else if(fonts){
            for(const [span,face] of entry.runFonts)fonts.apply(span,face);
        }
    }
    function remove(entry){entry.richPreview?.element.remove();entry.element.remove();}
    function clear(){if(measureElement)measureElement.textContent='';}
    function dispose(){measureElement?.remove();measureElement=null;}
    return {isText,apply,fitSize,editorOffset,truncateText,remove,clear,dispose};
})();
