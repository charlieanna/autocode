const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const root=process.env.AUTOCODE_TEST_SOURCE_ROOT||path.resolve(__dirname,'../../..');
const evidence=process.env.SCOPED_START_EVIDENCE_DIR||path.join(root,'.autocode/evidence/scoped-start','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});let server;const session='scoped-start-'+process.pid;
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expr=>browser('wait','--fn',expr);
const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function start(){return new Promise((resolve,reject)=>{
 server=spawn(process.env.AUTOCODE_TEST_PYTHON,[process.env.SCOPED_START_FIXTURE||path.join(__dirname,'scoped_start_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
 let output='',errors='';server.stdout.on('data',c=>{output+=c;const line=output.split('\n').find(x=>x.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',c=>errors+=c);server.on('error',reject);server.on('exit',code=>reject(Error('fixture exited '+code+' '+errors)));
});}
(async()=>{
 const fixture=await start(),captures=[];
 const files=execFileSync('git',['ls-files','-z','--cached','--others','--exclude-standard'],{cwd:root,encoding:'utf8'}).split('\0').filter(p=>p&&/^(tools|tests|scenarios|test-scenarios)\//.test(p)&&/\.(py|js|css|html|json|svg)$/.test(p)&&fs.existsSync(path.join(root,p)));
 const source=Object.fromEntries([...new Set(files)].map(p=>[p,digest(path.join(root,p))]));
 const asset=path.join(root,'tools/dashboard/assets/connected.svg');
 assert.equal(digest(asset),'a15236b06826d7e7955893553d0c5e0eb36c6afc5a1f869375bff2cd9ea97b78','the Figma status asset remains byte-identical');
 const served=await fetch(fixture.url+'/static/connected.svg');assert.equal(served.status,200);
 assert.equal(crypto.createHash('sha256').update(Buffer.from(await served.arrayBuffer())).digest('hex'),digest(asset),'the real server serves the local asset');
 for(const [viewport,width,height] of [['desktop',1440,900],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  for(const [index,project] of fixture.projects.entries()){
   browser('open',fixture.url+'#conversation='+project.conversation);
   wait('latestConversation?.id==='+JSON.stringify(project.conversation)+'&&document.querySelector("#draft-conversation").classList.contains("scoped-start")');
   data('()=>{document.documentElement.dataset.theme="light";return true}');
   wait('document.fonts.status==="loaded"');
   assert.match(data('()=>document.querySelector("#scoped-start-card").textContent'),new RegExp(project.name));
   assert.match(data('()=>document.querySelector("#scoped-composer-scope").textContent'),new RegExp(project.name));
   assert.equal(data('()=>document.querySelector("#draft-send").disabled'),true);
   assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
   // Inspect the user's resting view, then independently check keyboard focus.
   browser('click','#scoped-start-card h2');
   const geometry=data('()=>{const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};return {sidebar:rect(".sidebar"),intro:rect("#scoped-start-card"),composer:rect("#draft-form .composer"),send:rect("#draft-send"),context:rect("#scoped-project-context")}}');
   assert.ok(geometry.intro.y>=0&&geometry.intro.bottom<=height,'scope card stays visible');
   assert.ok(geometry.composer.y>=0&&geometry.composer.bottom<=height+1,'composer stays visible');
   assert.ok(geometry.send.width>=44&&geometry.send.height>=44,'send has accessible hit area');
   if(viewport==='desktop'){assert.equal(geometry.sidebar.width,220);assert.equal(geometry.context.x,904);assert.equal(geometry.context.y,128);}
   else assert.equal(geometry.context.width,0,'phone foregrounds the scoped chat');
   if(viewport==='desktop'){
    wait('document.querySelector("#sync-state img")?.complete');
    const assetSlot=data('()=>{const i=document.querySelector("#sync-state img"),r=i.getBoundingClientRect();return {src:i.getAttribute("src"),width:r.width,height:r.height,naturalWidth:i.naturalWidth,naturalHeight:i.naturalHeight}}');
    assert.deepEqual(assetSlot,{src:'/static/connected.svg',width:6,height:6,naturalWidth:6,naturalHeight:6});
   }
   const file=path.join(evidence,viewport+'-'+project.name+'.png');browser('screenshot',file);
   captures.push({file,sha256:digest(file),viewport:{width,height},reference:(viewport==='desktop'?['462:7945','462:8099','462:8253']:['469:1070','469:1121','469:1172'])[index],project:project.name,geometry});
   const draft='Unsent idea for '+project.name+' '+viewport;browser('fill','#draft-text',draft);browser('reload');wait('latestConversation?.id==='+JSON.stringify(project.conversation)+'&&document.querySelector("#scoped-start-card")');assert.equal(data('()=>document.querySelector("#draft-text").value'),draft);
   browser('press','Tab');browser('press','Shift+Tab');
   const focus=data('()=>{document.querySelector("#draft-text").focus();const s=getComputedStyle(document.querySelector("#draft-form .composer"));return {outline:s.outlineStyle,width:parseFloat(s.outlineWidth),shadow:s.boxShadow}}');
   assert.notEqual(focus.outline,'none');assert.ok(focus.width>=2);assert.notEqual(focus.shadow,'none');
   browser('fill','#draft-text','');
   assert.equal(fs.readFileSync(fixture.provider_calls,'utf8'),'','opening, refreshing and drafting never spends');
  }
 }
 // Native scoped creation saves a new conversation and preserves its scope.
 browser('set','viewport','1440','900');browser('click','.project-group-new[aria-label="New conversation in AutoCode"]');
 wait('latestConversation?.project_workspace==='+JSON.stringify(fixture.projects[0].workspace)+'&&document.querySelector("#scoped-start-card")');
 assert.ok(!fixture.projects.map(x=>x.conversation).includes(data('()=>latestConversation.id')));
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 for(const [file,sha]of Object.entries(source))assert.equal(digest(path.join(root,file)),sha,'source stable: '+file);
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({source_sha256:source,captures,no_provider_calls:fs.readFileSync(fixture.provider_calls,'utf8')==='',errors},null,2));
 console.log('Six scoped-conversation screens passed with persisted drafts, actual project creation, native geometry and zero model calls. '+evidence);
})().catch(error=>{console.error(error.stack||error);try{browser('screenshot',path.join(evidence,'failure.png'));console.error(JSON.stringify(data('()=>({view:currentView,title:document.querySelector("#draft-title").textContent,notice:document.querySelector("#dashboard-notice").textContent})')));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close')}catch{}if(server)server.kill('SIGTERM');});
