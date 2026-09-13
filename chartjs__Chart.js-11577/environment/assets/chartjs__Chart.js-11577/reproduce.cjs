const fs=require('fs');const esbuild=require('/opt/iid/node_modules/esbuild');const dir='/testbed/.reproduction';fs.mkdirSync(dir,{recursive:true});
(async()=>{
await esbuild.build({entryPoints:['/testbed/src/index.umd.ts'],bundle:true,platform:'browser',format:'iife',outfile:dir+'/chart.js',nodePaths:['/opt/iid/node_modules']});
fs.writeFileSync(dir+'/output.html',`<!doctype html><meta charset=utf-8><canvas id=c width=700 height=400></canvas><script>${fs.readFileSync(dir+'/chart.js','utf8')}</script><script>new Chart(document.getElementById('c'),{type:'line',data:{datasets:[{data:[{x:0,y:0},{x:10,y:1}]}]},options:{responsive:false,animation:false,scales:{x:{type:'linear',position:'top',ticks:{callback:(v,i,t)=>i===0||i===t.length-1?'FOO':'0',color:'green',backdropColor:'yellow',showLabelBackdrop:true,align:'inner'}}}}});</script>`);
console.log(dir+'/output.html');
})().catch(e=>{console.error(e);process.exitCode=1});
