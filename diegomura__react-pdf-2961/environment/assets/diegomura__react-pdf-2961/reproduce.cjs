const fs=require('fs');const esbuild=require('/opt/iid/node_modules/esbuild');const dir='/testbed/.reproduction';fs.mkdirSync(dir,{recursive:true});
(async()=>{
for(const name of ['inheritProps','layoutText']) await esbuild.build({entryPoints:['/testbed/packages/layout/src/svg/'+name+'.js'],bundle:true,packages:'external',platform:'node',format:'esm',outfile:dir+'/'+name+'.mjs'});
const inherit=(await import('file://'+dir+'/inheritProps.mjs')).default;const layout=(await import('file://'+dir+'/layoutText.mjs')).default;
const node={type:'TEXT',props:{x:10,y:30,fontSize:18,fontFamily:'Helvetica'},children:[['test','black'],['test','red'],['1234','black']].map(([value,fill])=>({type:'TSPAN',props:{fill},children:[{type:'TEXT_INSTANCE',value}]}))};
const drawn=layout(null,inherit(node));fs.writeFileSync(dir+'/layout.json',JSON.stringify(drawn,null,2));
const lines=drawn.children.map(c=>c.lines[0]);
fs.writeFileSync(dir+'/output.svg','<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100">'+lines.map(l=>'<text x="'+l.box.x+'" y="30" font-size="18" font-family="Arial" fill="'+l.runs[0].attributes.color+'">'+l.string+'</text>').join('')+'</svg>');
console.log(dir+'/output.svg (visualization of repository text layout; layout.json contains precise positions)');
})().catch(e=>{console.error(e);process.exitCode=1});
