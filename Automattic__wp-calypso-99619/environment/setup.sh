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
const component='client/my-sites/stats/stats-notices/do-you-love-jetpack-stats-notice.tsx';
const mockCode=`import React from 'react';
export const recordTracksEvent=()=>{};
export const isEnabled=()=>false;
export const PLAN_PREMIUM='premium';export const getPlan=()=>({getTitle:()=> 'Premium'});
export const HelpCenter={register:()=> 'help'};
export const localizeUrl=x=>x;export const useHasEnTranslation=()=>()=>true;
export const useDispatch=()=>()=>{};
export const Icon=()=>null;export const external={};
export const useTranslate=()=> (s,o)=>s.replace('%(product)s',o?.args?.product||'Jetpack Stats').replace('%s',typeof o?.args==='string'?o.args:'');
export const useSelector=()=>false;
export const STATS_PRODUCT_NAME='Jetpack Stats';
export const STATS_DO_YOU_LOVE_JETPACK_STATS_NOTICE='love';
export const trackStatsAnalyticsEvent=()=>{};
export const toggleUpsellModal=()=>({});
const empty=()=>({mutateAsync:()=>{}});empty.base=()=>{};
export default empty;`;
const mocks={name:'isolated-notice-dependencies',setup(build){
  build.onResolve({filter:/^(@automattic\/|@wordpress\/|calypso\/|i18n-calypso$|react-redux$)/},args=>({path:args.path,namespace:'notice-mock'}));
  build.onResolve({filter:/^\.\.\/(constants|utils)$/},args=>args.importer.endsWith('do-you-love-jetpack-stats-notice.tsx')?{path:args.path,namespace:'notice-mock'}:null);
  build.onLoad({filter:/.*/,namespace:'notice-mock'},args=>{
    if(args.path==='@automattic/components/src/notice-banner') return {contents:`import React from 'react'; export default function Banner(p){return React.createElement('section',{className:'notice-banner'},React.createElement('h3',{},p.title),p.children,React.createElement('button',{'aria-label':'Dismiss',onClick:p.onClose},'Close'));}`,resolveDir:'/opt/iid',loader:'js'};
    if(args.path==='@wordpress/data') return {contents:`export const useDispatch=()=>({setShowHelpCenter:()=>{},setShowSupportDoc:()=>{}});`,loader:'js'};
    return {contents:mockCode,resolveDir:'/opt/iid',loader:'js'};
  });
}};
(async()=>{
  await h.jasmineNode('client/my-sites/stats/stats-notices/lib/test/remove-stats-purchase-success-param.js',[mocks]);
  if(process.argv[2]==='full') {
    const entry=`import React from 'react';import {createRoot} from 'react-dom/client';import {flushSync} from 'react-dom';import Notice from '/testbed/${component}';
      let root;window.renderNotice=p=>{if(root)root.unmount();document.body.innerHTML='<div class="wp-admin"><main class="stats" id="app"></main></div>';root=createRoot(document.querySelector('#app'));flushSync(()=>root.render(React.createElement(Notice,p)));};`;
    await h.esbuild.build({stdin:{contents:entry,resolveDir:'/opt/iid',loader:'jsx'},bundle:true,format:'iife',platform:'browser',
      jsx:'automatic',nodePaths:['/opt/iid/node_modules'],plugins:[mocks],outfile:h.out+'/notice.js'});
    const sass=require('sass');
    const css=sass.compile(h.root+'/apps/odyssey-stats/src/styles/wp-admin.scss').css;
    const b=await h.browser();
    try{
      await b.page.setContent('<html><head></head><body></body></html>');
      await b.page.addStyleTag({content:`body{background:#fdfdfd;--jetpack-white-off:#f8f8f7} .stats{width:750px}.inner-notice-container{padding-top:32px}.notice-banner{background:white;border:1px solid #ddd;padding:16px} `+css});
      await b.page.addScriptTag({path:h.out+'/notice.js'});
      await require(h.root+'/.iid/f2p.cjs')({...h,page:b.page});
    }finally{await b.browser.close();}
  }
  h.finish();
})().catch(e=>{console.error(e);process.exitCode=2;});

IID_RUNNER
ln -s /opt/iid/node_modules /testbed/node_modules
