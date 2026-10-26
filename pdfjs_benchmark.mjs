import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const roots=[process.env.PDF_NODE_MODULES,path.join(path.dirname(fileURLToPath(import.meta.url)),'node_modules'),path.join(process.cwd(),'node_modules'),path.join(path.dirname(process.execPath),'..','node_modules')].filter(Boolean);
let require;
for(const root of roots){try{const r=createRequire(path.join(root,'..','package.json'));r.resolve('pdfjs-dist');require=r;break;}catch{}}
if(!require)throw new Error('Install pdfjs-dist and @napi-rs/canvas, or set PDF_NODE_MODULES.');
const {createCanvas,DOMMatrix,ImageData,Path2D}=require('@napi-rs/canvas');
Object.assign(globalThis,{DOMMatrix,ImageData,Path2D});
const pdfjs=await import(pathToFileURL(require.resolve('pdfjs-dist/legacy/build/pdf.mjs')));
const packageRoot=path.dirname(require.resolve('pdfjs-dist/package.json'));
const documentOptions={
  disableFontFace:true,stopAtErrors:true,verbosity:0,cMapPacked:true,
  cMapUrl:path.join(packageRoot,'cmaps')+'/',
  standardFontDataUrl:path.join(packageRoot,'standard_fonts')+'/',
  wasmUrl:path.join(packageRoot,'wasm')+'/',
  iccUrl:path.join(packageRoot,'iccs')+'/'
};
const [file,pageNumber,mode='render',widthArg='1404',png]=process.argv.slice(2);
const number=Number(pageNumber),width=Number(widthArg);
if(mode!=='compare-worker'&&(!Number.isInteger(number)||number<1||!Number.isInteger(width)||width<1))throw new Error('Invalid benchmark page/width.');
const compareSnapshots=(a,b,page,allow)=>{
  if(a.width!==b.width||a.height!==b.height)throw new Error('PDF.js dimensions changed.');
  let peak=0,sum=0,outside=0;const ink=new Uint8Array(a.width*a.height);
  for(let i=0;i<ink.length;i++)ink[i]=Math.round(.299*a.data[i*4]+.587*a.data[i*4+1]+.114*a.data[i*4+2])<250?1:0;
  for(let i=0;i<ink.length;i++){
    let changed=false;
    for(let c=0;c<3;c++){const delta=Math.abs(a.data[i*4+c]-b.data[i*4+c]);peak=Math.max(peak,delta);sum+=delta;changed ||= delta>0;}
    if(changed&&allow){const x=i%a.width,y=Math.floor(i/a.width);let lo=1,hi=0;
      for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){const j=Math.min(a.height-1,Math.max(0,y+dy))*a.width+Math.min(a.width-1,Math.max(0,x+dx));lo=Math.min(lo,ink[j]);hi=Math.max(hi,ink[j]);}
      if(lo===hi)outside++;
    }
  }
  const mean=sum/(ink.length*3),ok=peak===0||(allow&&peak<=96&&mean<=1.5&&outside===0);
  return {page,width:a.width,height:a.height,ok,exact:peak===0,peak,mean,outsideEdgeBand:outside};
};
if(mode==='preflight'){console.log(JSON.stringify({version:pdfjs.version,dependencies:'ready'}));}
else if(mode==='compare-worker'){
  const candidate=pageNumber,workerWidth=Number(widthArg),allow=png==='edges';
  if(!Number.isInteger(workerWidth)||workerWidth<1)throw new Error('Invalid verification width.');
  const tasks=[file,candidate].map(name=>pdfjs.getDocument({...documentOptions,data:new Uint8Array(fs.readFileSync(name))}));
  const [sourceDocument,candidateDocument]=await Promise.all(tasks.map(task=>task.promise));
  async function snapshot(document,page){
    const p=await document.getPage(page),initial=p.getViewport({scale:1}),v=p.getViewport({scale:workerWidth/initial.width});
    const canvas=createCanvas(Math.ceil(v.width),Math.ceil(v.height));
    await p.render({canvasContext:canvas.getContext('2d'),viewport:v,annotationMode:pdfjs.AnnotationMode.ENABLE}).promise;
    return {width:canvas.width,height:canvas.height,data:canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data};
  }
  process.stdin.setEncoding('utf8');let pending='';
  for await(const chunk of process.stdin){pending+=chunk;let split;
    while((split=pending.indexOf('\n'))>=0){const line=pending.slice(0,split).trim();pending=pending.slice(split+1);if(!line)continue;
      const page=Number(line);
      try{const a=await snapshot(sourceDocument,page);const b=await snapshot(candidateDocument,page);console.log(JSON.stringify(compareSnapshots(a,b,page,allow)));}
      catch(error){console.log(JSON.stringify({page,error:String(error?.message||error)}));}
    }
  }
  await Promise.all(tasks.map(task=>task.destroy()));
}
else if(mode==='compare'){
  async function snapshot(name){
    const task=pdfjs.getDocument({...documentOptions,data:new Uint8Array(fs.readFileSync(name))}),d=await task.promise;
    const p=await d.getPage(number),initial=p.getViewport({scale:1}),v=p.getViewport({scale:width/initial.width});
    const canvas=createCanvas(Math.ceil(v.width),Math.ceil(v.height));
    await p.render({canvasContext:canvas.getContext('2d'),viewport:v,annotationMode:pdfjs.AnnotationMode.ENABLE}).promise;
    const result={width:canvas.width,height:canvas.height,data:canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data};
    await task.destroy();return result;
  }
  const a=await snapshot(file);globalThis.gc?.();const b=await snapshot(png);
  const result=compareSnapshots(a,b,number,process.argv[7]==='edges');console.log(JSON.stringify(result));
  process.exitCode=result.ok?0:1;
}else{
const measure=async fn=>{const t=performance.now(),cpu=process.cpuUsage();const result=await fn();const c=process.cpuUsage(cpu);return {result,wall:(performance.now()-t)/1000,cpu:(c.user+c.system)/1e6,rss:process.memoryUsage().rss};};
const start=performance.now(),startCPU=process.cpuUsage();
const task=pdfjs.getDocument({...documentOptions,data:new Uint8Array(fs.readFileSync(file))});
const loaded=await measure(()=>task.promise);
const doc=loaded.result,page=await doc.getPage(number),view=page.getViewport({scale:1});
const viewport=page.getViewport({scale:width/view.width});
const output={version:pdfjs.version,file,page:number,mode,width,height:Math.ceil(viewport.height),load:{wall:loaded.wall,cpu:loaded.cpu},rss:loaded.rss};
if(mode==='prepare'){
  const annotations=await measure(()=>page.getAnnotations({intent:'display'}));
  const prepared=await measure(()=>page.getOperatorList({intent:'display',annotationMode:pdfjs.AnnotationMode.ENABLE}));
  output.annotations={wall:annotations.wall,cpu:annotations.cpu,count:annotations.result.length};
  output.prepare={wall:prepared.wall,cpu:prepared.cpu};output.operators=prepared.result.fnArray.length;
  output.rss=Math.max(output.rss,annotations.rss,prepared.rss);
}else{
  let canvas;
  const draw=()=>{canvas=createCanvas(Math.ceil(viewport.width),Math.ceil(viewport.height));return page.render({canvasContext:canvas.getContext('2d'),viewport,annotationMode:pdfjs.AnnotationMode.ENABLE}).promise;};
  const cold=await measure(draw);
  const c=process.cpuUsage(startCPU);
  output.cold={wall:cold.wall,cpu:cold.cpu};output.ready={wall:(performance.now()-start)/1000,cpu:(c.user+c.system)/1e6};
  const cached=await measure(draw);output.cached={wall:cached.wall,cpu:cached.cpu};output.rss=Math.max(output.rss,cold.rss,cached.rss);
  output.pixelHash=createHash('sha256').update(canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data).digest('hex');
  if(png)fs.writeFileSync(png,canvas.toBuffer('image/png'));
}
console.log(JSON.stringify(output));await task.destroy();
}
