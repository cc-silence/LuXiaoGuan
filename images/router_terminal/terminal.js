"use strict";
const term=new Terminal({scrollback:2000,fontFamily:'"Cascadia Mono",Consolas,"Microsoft YaHei",monospace',fontSize:14,disableStdin:true,allowProposedApi:false,convertEol:false});
const fit=new FitAddon.FitAddon();const search=new SearchAddon.SearchAddon();
term.loadAddon(fit);term.loadAddon(search);term.open(document.getElementById("terminal"));
let bridge=null;
let hoveredLink="";
new QWebChannel(qt.webChannelTransport,function(channel){
 bridge=channel.objects.bridge;
 term.loadAddon(new WebLinksAddon.WebLinksAddon((event,uri)=>bridge.openUrl(uri),{
   hover:(event,uri)=>{hoveredLink=uri;},leave:()=>{hoveredLink="";}
 }));
 term.options.linkHandler={activate:(event,uri)=>bridge.openUrl(uri),hover:(event,uri)=>{hoveredLink=uri;},leave:()=>{hoveredLink="";}};
 term.onData(data=>bridge.input(data));
 term.onResize(size=>bridge.resize(size.cols,size.rows));
 bridge.output.connect(encoded=>{
   const binary=atob(encoded);const data=Uint8Array.from(binary,c=>c.charCodeAt(0));
   term.write(data,()=>bridge.ack(data.length));
 });
 bridge.control.connect(encoded=>{
   const value=JSON.parse(encoded);
   if(value.action==="theme"){
     document.body.dataset.theme=value.light?"light":"dark";
     const style=getComputedStyle(document.body);
     const color=name=>style.getPropertyValue("--terminal-"+name).trim();
     const theme={background:color("background"),foreground:color("foreground"),
       cursor:color("foreground"),selectionBackground:color("selection")};
     if(JSON.stringify(term.options.theme)!==JSON.stringify(theme))term.options.theme=theme;
     if(term.options.fontSize!==value.fontSize){term.options.fontSize=value.fontSize;fit.fit();}
   }
   if(value.action==="state"){term.options.disableStdin=!value.connected;}
   if(value.action==="copy"){const text=term.getSelection();if(text)bridge.copy(text);}
   if(value.action==="selectAll"){term.selectAll();}
   if(value.action==="clear"){term.clear();}
   if(value.action==="reset"){term.reset();}
   if(value.action==="paste"){term.paste(value.text);}
   if(value.action==="find"){search.findNext(value.text);}
   if(value.action==="focus"){fit.fit();term.focus();}
 });
 term.onSelectionChange(()=>{const text=term.getSelection();bridge.selectionChanged(Boolean(text));if(text)bridge.copy(text);});
 term.element.addEventListener("paste",event=>{
   event.preventDefault();event.stopImmediatePropagation();
   bridge.paste(event.clipboardData.getData("text/plain"));
 },true);
 term.element.addEventListener("contextmenu",event=>{event.preventDefault();event.stopImmediatePropagation();bridge.contextMenu(Math.round(event.clientX),Math.round(event.clientY),term.hasSelection(),hoveredLink);});
 term.element.addEventListener("mousedown",event=>{
   const viewport=term.element.querySelector(".xterm-viewport");
   const rect=viewport.getBoundingClientRect();
   const gutter=viewport.offsetWidth-viewport.clientWidth;
   if(event.target===viewport && gutter>0 && event.clientX>=rect.right-gutter){event.stopImmediatePropagation();}
 },true);
 term.element.addEventListener("wheel",event=>{
   if(!event.ctrlKey)return;
   event.preventDefault();event.stopImmediatePropagation();
   if(event.deltaY)bridge.zoom(event.deltaY<0?1:-1);
 },{capture:true,passive:false});
 term.attachCustomKeyEventHandler(event=>{
   if(event.type!=="keydown")return true;
   if(event.ctrlKey&&event.key.toLowerCase()==="f"){bridge.findRequested();return false;}
   if(event.ctrlKey&&event.shiftKey&&event.key.toLowerCase()==="v"){bridge.requestPaste();return false;}
   return true;
 });
 new ResizeObserver(()=>fit.fit()).observe(document.getElementById("terminal"));
 bridge.ready();fit.fit();term.focus();
});
