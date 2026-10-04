// Adapt raw saved-record fixtures through the production server projection.
const path=require('node:path'),{execFileSync}=require('node:child_process'),cache=new Map();
module.exports=run=>{
 const serialized=JSON.stringify(run);if(cache.has(serialized))return JSON.parse(cache.get(serialized));
 const directory=path.resolve(__dirname,'..');
 const code='import json,sys;sys.path.insert(0,sys.argv[1]);from dashboard_transcript import project;print(json.dumps(project(json.load(sys.stdin))))';
 const output=execFileSync(process.env.AUTOCODE_TEST_PYTHON||'python3',['-c',code,directory],{input:serialized,encoding:'utf8',timeout:10000});
 cache.set(serialized,output);return JSON.parse(output);
};
