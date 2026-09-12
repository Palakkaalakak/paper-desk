// Development recovery only. Run with pm2 start checkpoint.cjs --name paper-recovery.
// Never used by the production application. Stop before manual git operations.
const {spawn}=require('node:child_process');
let busy=false;
function git(args,input){return new Promise((resolve,reject)=>{const p=spawn('git',args,{cwd:__dirname,stdio:['pipe','pipe','pipe']});let out='',err='';p.stdout.on('data',b=>out+=b);p.stderr.on('data',b=>err+=b);p.on('error',reject);p.on('close',c=>c?reject(Error(err||'git failed')):resolve(out.trimEnd()));p.stdin.end(input);});}
async function checkpoint(){if(busy)return;busy=true;try{
 if(await git(['branch','--show-current'])!=='main')throw Error('Recovery requires main branch');
 const files=(await git(['ls-files','-z'])).split('\0').filter(f=>f&&!/(^|\/)(\.env(?:\.|$)|\.dev\.vars|node_modules|__pycache__|\.venv)/.test(f)&&!/(?:secret|credential)|\.(?:pem|key|log|pyc|zip|tar\.gz)$/i.test(f));
 await git(['add','-A','--pathspec-from-file=-','--pathspec-file-nul'],files.join('\0')+'\0');
 if(await git(['diff','--cached','--name-only']))await git(['commit','-m','Checkpoint GUNS implementation '+new Date().toISOString()]);
 await git(['push','origin','main']);console.log(new Date().toISOString(),'Checkpoint pushed to GitHub main');
 }catch(e){console.error('Recovery:',e.message.replace(/https:\/\/[^\s]+/g,'[remote]'));}finally{busy=false;}}
checkpoint();setInterval(checkpoint,30000);
