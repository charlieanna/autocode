const vm=require('node:vm'),path=require('node:path'),{execFileSync}=require('node:child_process');
const names=JSON.parse(execFileSync(process.env.AUTOCODE_TEST_PYTHON||'python3',
 ['-c','import json; from autocode_role_names import CATALOGUE; print(json.dumps(CATALOGUE))'],
 {cwd:path.resolve(__dirname,'../..'),encoding:'utf8'}));
module.exports={...vm,names,createContext:(sandbox={},options)=>vm.createContext({...sandbox,AUTOCODE_ROLE_NAMES:structuredClone(names)},options)};
