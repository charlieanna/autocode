const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const vm=require('./dashboard_vm');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const names=JSON.parse(fs.readFileSync(path.join(__dirname,'../../autocode_role_names.json'),'utf8'));
const context=vm.createContext({human:value=>String(value||'').replaceAll('_',' ')});
vm.runInContext(source.slice(source.indexOf('function jointPlanning('),source.indexOf('function statusAge(')),context);
for(const mode of [undefined,...Object.keys(names.modes)])for(const [stage,entry] of Object.entries(names.stages)){
 const actual=names.modes[mode]?.[stage]||entry;
 for(const suffix of ['','_report_repair'])assert.equal(context.stageName({stage:stage+suffix,monitor:{workflow_mode:mode}}),actual.role+' · '+actual.activity);
}
assert.equal(context.stageName({stage:'astra_plan'}),'Planner · Assigning implementation');
assert.equal(context.stageName({stage:'requirements_gather'}),'Requirements · Gathering requirements');
for(const mode of Object.keys(names.modes))assert.match(context.stageName({stage:'astra_checkpoint',monitor:{workflow_mode:mode}}),/^Validator \/ Completion Owner/);
assert.equal(context.roleDisplayName('plan_reviewer'),'Plan Reviewer');
assert.equal(context.roleDisplayName('requirements'),'Requirements');
console.log('Every dashboard stage and report repair uses the shared job names, including reviewer-routing modes.');
