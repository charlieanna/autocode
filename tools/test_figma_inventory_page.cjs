// Offline collector protocol: frozen nodes refuse every canvas mutation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const source = fs.readFileSync(__dirname + '/figma_inventory_page.js','utf8').replace('"FILE_KEY"','"FILEA"').replace('"PAGE_ID"','"0:1"');
function freeze(value, seen = new Set()) {
  if (!value || typeof value !== 'object' || seen.has(value)) return value;
  seen.add(value);
  for (const field of Object.values(value)) freeze(field,seen);
  Object.freeze(value);
  return value;
}
function fixture(missing = false) {
  const component = {id:'1:4',type:'COMPONENT',name:'Primary',key:'primary',variantProperties:{Type:'primary'}};
  const set = {id:'1:3',type:'COMPONENT_SET',name:'Button',key:'LIBRARY',children:[component]};
  component.parent=set;
  const text = {id:'I4:5;10:12',type:'TEXT',name:'Label',fontName:Symbol('mixed'),
    getStyledTextSegments:() => [{fontName:{family:'Inter',style:'Regular'}},{fontName:{family:'Inter',style:'SemiBold'}}]};
  const image = {id:'1:7',type:'RECTANGLE',name:'Image',fills:[{type:'IMAGE',imageHash:'image',boundVariables:{opacity:{type:'VARIABLE_ALIAS',id:'v1'}}}],
    reactions:[{trigger:{type:'ON_CLICK'},actions:[{type:'URL',url:'https://example.test/'},{type:'BACK'}]}]};
  const vector = {id:'1:8',type:'VECTOR',name:'Icon',reactions:[{trigger:{type:'ON_CLICK'},actions:[
    {type:'NODE',destinationId:'1:2',navigation:'NAVIGATE'},
    {type:'SET_VARIABLE',variableId:'v1',variableValue:{type:'VARIABLE_ALIAS',id:'v2'}},
    {type:'SET_VARIABLE_MODE',variableCollectionId:'c1',variableModeId:'dark'}]}]};
  const instance = {id:'1:9',type:'INSTANCE',name:'Button',getMainComponentAsync:async() => component};
  const frame = {id:'1:2',type:'FRAME',name:'Home',width:1440,height:900,children:[text,image,vector,instance]};
  const pageTarget = {id:'0:1',type:'PAGE',name:'Main',children:[frame,set]};
  function rest(node) {
    return {id:node.id,type:node.type === 'PAGE' ? 'CANVAS' : node.type,name:node.name,
      ...(node.width ? {width:node.width,height:node.height} : {}),
      ...(node.children ? {children:node.children.map(rest)} : {}),fills:node.fills || []};
  }
  const original=rest(pageTarget);
  pageTarget.exportAsync=async request => {assert.equal(request.format,'JSON_REST_V1');return {document:original};};
  freeze(pageTarget);freeze(original);
  const page=new Proxy(pageTarget,{set(){throw new Error('Canvas mutation refused');},deleteProperty(){throw new Error('Canvas mutation refused');}});
  const collection=freeze({id:'c1',name:'Theme',variableIds:['v1','unused'],modes:[{modeId:'light',name:'Light'},{modeId:'dark',name:'Dark'}]});
  const variables=freeze({v1:{id:'v1',key:'color',name:'Foreground',variableCollectionId:'c1',resolvedType:'COLOR',
    valuesByMode:{light:{r:0,g:0,b:0},dark:{type:'VARIABLE_ALIAS',id:'v2'}}},
    v2:{id:'v2',key:'remote',name:'Remote gray',variableCollectionId:'c1',resolvedType:'COLOR',valuesByMode:{light:{r:.3,g:.3,b:.3},dark:{r:.5,g:.5,b:.5}}},
    unused:{id:'unused',key:'spacing',name:'Unused spacing',variableCollectionId:'c1',resolvedType:'FLOAT',valuesByMode:{light:8,dark:12}}});
  const before=JSON.stringify({original,variables,collection});
  let switches=0;
  const api=freeze({fileKey:'FILEA',mixed:text.fontName,getNodeByIdAsync:async id=>id==='0:1'?page:null,
    setCurrentPageAsync:async target=>{assert.equal(target,page);switches++;},variables:{
      getLocalVariablesAsync:async()=>[variables.v1,variables.unused],
      getLocalVariableCollectionsAsync:async()=>[collection],
      getVariableByIdAsync:async id=>missing&&id==='v2'?null:variables[id],
      getVariableCollectionByIdAsync:async id=>id==='c1'?collection:null}});
  return {api,page,before,unchanged:()=>JSON.stringify({original,variables,collection})===before,switches:()=>switches};
}
(async()=>{
  const sound=fixture();
  const receipt=await new AsyncFunction('figma',source)(sound.api);
  assert.equal(receipt.complete,true);assert.equal(receipt.read_only,true);assert.deepEqual(receipt.errors,[]);
  assert.equal(receipt.nodes.length,8);assert.equal(receipt.variables.length,6);
  assert.deepEqual(receipt.nodes.find(n=>n.type==='TEXT').fonts,[{family:'Inter',style:'Regular'},{family:'Inter',style:'SemiBold'}]);
  assert.equal(receipt.nodes.find(n=>n.type==='INSTANCE').component_ref.key,'LIBRARY');
  assert.equal(receipt.nodes.flatMap(n=>n.assets).length,2);
  assert.equal(receipt.nodes.flatMap(n=>n.transitions).length,5);
  assert.equal(sound.switches(),1);assert(sound.unchanged());
  const broken=fixture(true);
  const unavailable=await new AsyncFunction('figma',source)(broken.api);
  assert.equal(unavailable.complete,false);assert(unavailable.errors.some(e=>e.includes('v2')));assert(broken.unchanged());
  await assert.rejects(new AsyncFunction('figma',source.replace('await figma.setCurrentPageAsync(page);','page.name = "changed";'))(fixture().api),/mutation refused/);
  await assert.rejects(new AsyncFunction('figma',source)(Object.freeze({...fixture().api,fileKey:'OTHER'})),/Wrong Figma file/);
  console.log('Read-only collector: typography, paints, modes, aliases, instances, actions and refusal controls passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
