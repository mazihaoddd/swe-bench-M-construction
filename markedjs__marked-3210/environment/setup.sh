#!/bin/bash
set -euo pipefail
cat > /opt/iid/common.cjs <<'IID_COMMON'
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const esbuild = require('esbuild');
const {spawnSync} = require('node:child_process');
const results = [];
const root = '/testbed';
const out = path.join(root, '.iid-results');
fs.mkdirSync(out, {recursive:true});
async function check(id, fn) {
  try { await fn(); results.push({id,status:'PASS',detail:''}); }
  catch(e) { results.push({id,status:e.code==='ERR_ASSERTION'?'FAIL':'ERROR',detail:e.stack||String(e)}); }
}
function command(argv, {allowFailure=false}={}) {
  const p=spawnSync(argv[0],argv.slice(1),{cwd:root,encoding:'utf8',timeout:240000,maxBuffer:20*1024*1024});
  if(p.error || (p.status!==0 && !allowFailure)) throw Error((p.error||'')+'\n'+p.stdout+'\n'+p.stderr);
  return p.stdout;
}
async function bundle(entry, filename, options={}) {
  await esbuild.build({entryPoints:[path.join(root,entry)],bundle:true,platform:'node',format:'esm',
    packages:'external',nodePaths:['/opt/iid/node_modules'],outfile:path.join(out,filename),...options});
  return path.join(out,filename);
}
async function browser() {
  const b=await require('puppeteer-core').launch({executablePath:'/usr/bin/chromium',headless:true,
    args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader',
      '--enable-unsafe-swiftshader','--disable-background-timer-throttling']});
  const page=await b.newPage();
  await page.setViewport({width:900,height:600,deviceScaleFactor:1});
  page.on('pageerror', e=>process.stderr.write('BROWSER: '+e.message+'\n'));
  return {browser:b,page};
}
async function jasmineNode(file, plugins=[]) {
  const compiled=await bundle(file,'p2p.cjs',{format:'cjs',plugins});
  const Jasmine=require('jasmine'); const j=new Jasmine();
  j.exitOnCompletion=false;
  j.env.configure({random:false});
  j.env.addReporter({specDone:r=>results.push({id:'P2P::'+r.fullName,
    status:r.status==='passed'?'PASS':r.status==='failed'?'FAIL':'SKIP',
    detail:r.failedExpectations.map(e=>e.message).join('\n')})});
  j.loadConfig({spec_files:[compiled],random:false});
  await new Promise(resolve=>{j.env.addReporter({jasmineDone:resolve});j.execute();});
}
function finish() {
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({tests:results},null,2));
  for(const r of results) console.log(r.status+' '+r.id);
  process.exitCode=results.length && results.every(r=>r.status==='PASS')?0:1;
}
module.exports={fs,path,assert,esbuild,results,root,out,check,command,bundle,browser,jasmineNode,finish};

IID_COMMON
cat > /opt/iid/run.cjs <<'IID_RUNNER'
const h=require('./common.cjs');
(async()=>{
  const compiled=await h.bundle('src/marked.ts','marked.mjs');
  const marked=(await import('file://'+compiled)).marked;
  const {HtmlDiffer}=await import('@markedjs/html-differ');
  const differ=new HtmlDiffer({ignoreSelfClosingSlash:true,ignoreComments:false});
  const fixtures=['fences_breaking_paragraphs','fences_following_list','fences_following_nptable','fences_following_table'];
  for(const name of fixtures) await h.check('P2P::'+name,async()=>{
    const src=h.fs.readFileSync(h.root+'/test/specs/new/'+name+'.md','utf8');
    const expected=h.fs.readFileSync(h.root+'/test/specs/new/'+name+'.html','utf8');
    const actual=marked.parse(src);
    h.assert.ok(await differ.isEqual(expected,actual),JSON.stringify(await differ.diffHtml(actual,expected)));
  });
  if(process.argv[2]==='full') {
    const b=await h.browser();
    try { await require(h.root+'/.iid/f2p.cjs')({...h,marked,page:b.page}); }
    finally { await b.browser.close(); }
  }
  h.finish();
})().catch(e=>{console.error(e);process.exitCode=2;});

IID_RUNNER
ln -s /opt/iid/node_modules /testbed/node_modules
