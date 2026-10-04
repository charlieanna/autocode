// A stale completion changes its display, never its execution authority.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const context=vm.createContext({taskActionBusy:()=>false});
vm.runInContext('let taskReadError="";',context);
vm.runInContext(source.slice(source.indexOf('function statusInfo('),source.indexOf('function runtimeLabel(')),context);
vm.runInContext(source.match(/function canEditFutureTaskSettings\(run\)\{[^\n]+/)[0],context);
for(const freshness of [true,false,undefined]){
 const run={status:'TASK_COMPLETE',completion_current:freshness,interventions:{mode:'capable'}};
 assert.equal(context.statusInfo(run).group,freshness===false?'stopped':'complete');
 assert.equal(context.canEditFutureTaskSettings(run),false);
 if(freshness===false)assert.equal(context.statusInfo(run).stateLabel,'Completion needs verification');
}
console.log('Stale completion is visibly unverified and cannot gain settings mutation authority.');
