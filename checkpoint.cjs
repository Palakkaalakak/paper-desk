// Development recovery only. Never started by the production application.
// Run with: pm2 start checkpoint.cjs --name paper-recovery
// Stop with: pm2 delete paper-recovery
// New files must be deliberately registered with `git add -N file` or git add.
const {spawn} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const root = __dirname;
const branch = 'recovery/performance-work';
let busy = false;
function git(args, env = {}, input = null) {
  return new Promise((resolve, reject) => {
    const p = spawn('git', args, {cwd: root, env: {...process.env, ...env}, stdio: ['pipe', 'pipe', 'pipe']});
    let out = '', error = '';
    p.stdout.on('data', b => out += b);
    p.stderr.on('data', b => error += b);
    p.on('error', reject);
    p.on('close', code => code ? reject(new Error(error || 'git failed')) : resolve(out.trimEnd()));
    p.stdin.end(input);
  });
}
async function checkpoint() {
  if (busy) return;
  busy = true;
  let index;
  try {
    const gitDir = await git(['rev-parse', '--absolute-git-dir']);
    index = path.join(gitDir, 'recovery-index-' + process.pid);
    const env = {GIT_INDEX_FILE: index};
    const files = (await git(['ls-files', '-z'])).split('\0').filter(Boolean).filter(f =>
      !/(^|\/)(\.env(?:\.|$)|\.dev\.vars|\.contracts\.json|node_modules|__pycache__|\.venv)/.test(f) &&
      !/\.(?:pem|key|log|pyc|zip|tar\.gz)$/.test(f));
    let parent;
    try { parent = await git(['rev-parse', 'refs/heads/' + branch]); }
    catch { parent = await git(['rev-parse', 'HEAD']); }
    await git(['read-tree', 'HEAD'], env);
    await git(['add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul'], env, files.join('\0') + '\0');
    const tree = await git(['write-tree'], env);
    const previous = await git(['rev-parse', parent + '^{tree}']);
    if (tree !== previous) {
      const commit = await git(['commit-tree', tree, '-p', parent, '-m', 'Recovery checkpoint ' + new Date().toISOString()]);
      await git(['update-ref', 'refs/heads/' + branch, commit]);
    } else {
      await git(['update-ref', 'refs/heads/' + branch, parent]);
    }
    await git(['push', 'origin', 'refs/heads/' + branch + ':refs/heads/' + branch]);
    console.log(new Date().toISOString(), 'Recovery snapshot verified on GitHub');
  } catch (e) {
    console.error('Recovery checkpoint failed:', e.message.replace(/https:\/\/[^\s]+/g, '[remote]'));
  } finally {
    if (index) { try { fs.unlinkSync(index); } catch {} }
    busy = false;
  }
}
checkpoint();
setInterval(checkpoint, 30000);
