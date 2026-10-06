const fs=require('node:fs'),path=require('node:path');
const read=n=>fs.readFileSync(path.join(__dirname,n),'utf8');
const target=path.join(__dirname,'paper_local.html');let html=read('paper_local.html');
const safe=s=>s.replace(/<\/script/gi,'<\\/script');
const bundle='<!-- VMI_BUNDLE_START -->\n<style id="vmi-style">'+read('vmi.css')+'</style>\n<script id="vmi-source">window.VMI_SOURCE='+JSON.stringify(read('VMI_MASTER_DOCUMENT.md')).replace(/</g,'\\u003c')+';</script>\n<script id="vmi-engine">'+safe(read('vmi.js'))+'</script>\n<script id="paper-corrections">'+safe(read('paper-corrections.js'))+'</script>\n<script id="vmi-ui">'+safe(read('vmi-ui.js'))+'</script>\n<!-- VMI_BUNDLE_END -->';
if(html.includes('<!-- VMI_BUNDLE_START -->'))html=html.replace(/<!-- VMI_BUNDLE_START -->[\s\S]*?<!-- VMI_BUNDLE_END -->/,()=>bundle);
else{const anchor='<script>\nvar PAPER_LOCAL = true;';if(!html.includes(anchor))throw Error('Missing application anchor');html=html.replace(anchor,()=>bundle+'\n'+anchor);}
fs.writeFileSync(target,html);console.log('VMI embedded; Python asset allowlist unchanged');
