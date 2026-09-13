const fs=require('fs');const esbuild=require('/opt/iid/node_modules/esbuild');const dir='/testbed/.reproduction';fs.mkdirSync(dir,{recursive:true});
(async()=>{
await esbuild.build({entryPoints:['/testbed/src/marked.ts'],bundle:true,platform:'node',format:'esm',outfile:dir+'/marked.mjs'});
const {marked}=await import('file://'+dir+'/marked.mjs');
const text=fs.readFileSync(process.argv[2]||__dirname+'/input-1.md','utf8');
fs.writeFileSync(dir+'/output.html','<!doctype html><meta charset=utf-8><style>body{font:16px Arial}pre{background:#eee;padding:12px}</style>'+marked(text));
console.log(dir+'/output.html');
})().catch(e=>{console.error(e);process.exitCode=1});
