const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const names=JSON.parse(fs.readFileSync(path.join(__dirname,'../../autocode_role_names.json'),'utf8'));
module.exports={...vm,createContext:(sandbox={},options)=>vm.createContext({...sandbox,AUTOCODE_ROLE_NAMES:structuredClone(names)},options)};
