// Offline protocol: native-style nodes expose only id as an own property.
// Every node/returned property is frozen; canvas and getter-data writes refuse.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const source = fs.readFileSync(__dirname + '/figma_inventory_page.js','utf8').replace('"FILE_KEY"','"FILEA"').replace('"PAGE_ID"','"0:1"');
const mixed=Symbol('mixed');
function freeze(value,seen=new Set()) {
  if(!value || typeof value!=='object' || seen.has(value))return value;
  seen.add(value);for(const child of Object.values(value))freeze(child,seen);return Object.freeze(value);
}
const defaults={children:[],visible:true,locked:false,componentPropertyReferences:null,boundVariables:{},explicitVariableModes:{},resolvedVariableModes:{c1:'light'},
  animationStyles:[],animations:{},manualKeyframeTracks:{},timelines:[],x:0,y:0,width:100,height:20,minWidth:null,maxWidth:null,minHeight:null,maxHeight:null,
  relativeTransform:[[1,0,0],[0,1,0]],absoluteTransform:[[1,0,0],[0,1,0]],absoluteBoundingBox:{x:0,y:0,width:100,height:20},
  absoluteRenderBounds:{x:0,y:0,width:100,height:20},rotation:0,layoutSizingHorizontal:'FIXED',layoutSizingVertical:'FIXED',layoutAlign:'INHERIT',layoutGrow:0,
  layoutPositioning:'AUTO',gridRowAnchorIndex:0,gridColumnAnchorIndex:0,gridRowSpan:1,gridColumnSpan:1,gridChildHorizontalAlign:'AUTO',gridChildVerticalAlign:'AUTO',
  opacity:1,blendMode:'NORMAL',isMask:false,maskType:'ALPHA',effects:[],effectStyleId:'',fills:[],fillStyleId:'',strokes:[],strokeStyleId:'',strokeWeight:0,
  strokeJoin:'MITER',strokeAlign:'INSIDE',dashPattern:[],strokeGeometry:[],strokeCap:'NONE',strokeMiterLimit:4,fillGeometry:[],cornerRadius:0,cornerSmoothing:0,
  topLeftRadius:0,topRightRadius:0,bottomLeftRadius:0,bottomRightRadius:0,strokeTopWeight:0,strokeBottomWeight:0,strokeLeftWeight:0,strokeRightWeight:0,
  variableWidthStrokeProperties:{widthProfile:'UNIFORM'},complexStrokeProperties:{type:'BASIC'},layoutGrids:[],gridStyleId:'',clipsContent:true,guides:[],
  inferredAutoLayout:null,layoutMode:'NONE',paddingLeft:0,paddingRight:0,paddingTop:0,paddingBottom:0,primaryAxisSizingMode:'FIXED',counterAxisSizingMode:'FIXED',
  strokesIncludedInLayout:false,layoutWrap:'NO_WRAP',primaryAxisAlignItems:'MIN',counterAxisAlignItems:'MIN',counterAxisAlignContent:'AUTO',itemSpacing:0,
  counterAxisSpacing:0,itemReverseZIndex:false,gridRowCount:0,gridColumnCount:0,gridRowGap:0,gridColumnGap:0,gridRowSizes:[],gridColumnSizes:[],gridAutoTracks:false,
  gridItemsPositioning:'AUTO',overflowDirection:'NONE',numberOfFixedChildren:0,overlayPositionType:'CENTER',overlayBackground:{type:'NONE'},
  overlayBackgroundInteraction:'NONE',constraints:{horizontal:'MIN',vertical:'MIN'},targetAspectRatio:null,key:'',remote:false,componentProperties:{},scaleFactor:1,
  overrides:[],reactions:[],vectorPaths:[{windingRule:'NONZERO',data:'M0 0 L1 1'}],vectorNetwork:{vertices:[],segments:[],regions:[]},handleMirroring:'NONE',
  backgrounds:[],prototypeBackgrounds:[],flowStartingPoints:[],prototypeStartNode:null,hasMissingFont:false,fontSize:14,fontName:{family:'Inter',style:'Regular'},
  fontWeight:400,textCase:'ORIGINAL',openTypeFeatures:{},letterSpacing:{unit:'PIXELS',value:0},hyperlink:null,characters:'',textAlignHorizontal:'LEFT',
  textAlignVertical:'TOP',autoRename:false,textStyleId:'',paragraphIndent:0,paragraphSpacing:0,textWrapStyle:'AUTO',listSpacing:0,hangingPunctuation:false,
  hangingList:false,textDecoration:'NONE',textDecorationStyle:null,textDecorationOffset:null,textDecorationThickness:null,textDecorationColor:null,
  textDecorationSkipInk:null,lineHeight:{unit:'AUTO'},leadingTrim:'NONE',textAutoResize:'NONE',textTruncation:'DISABLED',maxLines:null,
  sectionContentsHidden:false,transformModifiers:[],arcData:{startingAngle:0,endingAngle:6.28,innerRadius:0},pointCount:5,innerRadius:.5,
  booleanOperation:'UNION',textPathStartData:{segment:0,position:0},fontStyle:'REGULAR',listOptions:{type:'NONE'},indentation:0,textStyleOverrides:[]};
function fixture(options={}) {
  let mutations=0,restCalls=0,switches=0;
  const models=[], proxies=[];
  function node(id,type,name,extra={}) {
    const model={...defaults,id,type,name,...extra};models.push(model);
    const prototype={};
    for(const field of Object.keys(model))if(field!=='id')Object.defineProperty(prototype,field,{get(){
      if(options.throwGetter===id+'.'+field)throw new Error('getter failure');
      return options.missingGetter===id+'.'+field ? undefined : model[field];
    }});
    const target=Object.create(Object.freeze(prototype));Object.defineProperty(target,'id',{value:id,enumerable:true});Object.freeze(target);
    const proxy=new Proxy(target,{get(target,field,receiver){
      if(field==='parent'||field==='exportAsync')return model[field];
      if(field==='componentPropertyDefinitions'&&type==='COMPONENT'&&model.parent?.type==='COMPONENT_SET')throw new Error('Cannot read variant definitions');
      return Reflect.get(target,field,receiver);
    },set(){mutations++;throw new Error('Canvas mutation refused');},deleteProperty(){mutations++;throw new Error('Canvas mutation refused');}});
    proxies.push(proxy);return proxy;
  }
  const segments=[{...defaults,characters:'Hi 😁',start:0,end:5,fontName:{family:'Inter',style:'Regular'},
    fills:[{type:'SOLID',color:{r:0,g:0,b:0},boundVariables:{color:{type:'VARIABLE_ALIAS',id:'v2'}}}]},
    {...defaults,characters:' friend',start:5,end:12,fontName:{family:'Inter',style:'Semi Bold',variationSettings:{wght:600,slnt:0}},
    fills:[{type:'IMAGE',imageHash:'rich-text-photo'}],boundVariables:{fontSize:{type:'VARIABLE_ALIAS',id:'v1'}}}];
  if(options.badSegment)segments[1].start=6;
  const component=node('1:4','COMPONENT','Type=primary',{key:'primary',variantProperties:{Type:'primary'}});
  const set=node('1:3','COMPONENT_SET','Button',{key:'LIBRARY',children:[component],componentPropertyDefinitions:{Type:{type:'VARIANT',defaultValue:'primary',variantOptions:['primary']}}});
  models[0].parent=set;
  // Variant-owned definitions are forbidden: only read the containing owner.
  const text=node('I4:5;10:12','TEXT','Label',{fontName:mixed,fills:mixed,characters:'Hi 😁 friend',getRangeTextWrapStyle:(start,end)=>{
    assert(segments.some(segment=>segment.start===start&&segment.end===end));return 'AUTO';},
    getStyledTextSegments:fields=>{assert(!fields.includes('textWrapStyle'));return segments.map(segment=>freeze(Object.fromEntries(['characters','start','end',...fields].filter(key=>key!=='boundVariables'||options.optionalBoundVariables!==false).map(key=>[key,segment[key]]))));}});
  const image=node('1:7','RECTANGLE','Image',{fills:[{type:options.paintType||'IMAGE',imageHash:'image',boundVariables:{opacity:{type:'VARIABLE_ALIAS',id:'v1'}}}],
    effects:[{type:'DROP_SHADOW',color:{r:0,g:0,b:0,a:1},offset:{x:0,y:2},radius:4,visible:true,blendMode:'NORMAL',boundVariables:{radius:{type:'VARIABLE_ALIAS',id:'v2'}}}],
    reactions:[{trigger:{type:'ON_CLICK'},actions:[{type:'URL',url:'https://example.test/'},{type:'BACK'}]}]});
  const vector=node('1:8','VECTOR','Icon',{reactions:[{trigger:{type:'ON_CLICK'},actions:[{type:'NODE',destinationId:'1:2',navigation:'NAVIGATE'},
    {type:'SET_VARIABLE',variableId:'v1',variableValue:{type:'VARIABLE_ALIAS',id:'v2'}},{type:'SET_VARIABLE_MODE',variableCollectionId:'c1',variableModeId:'dark'}]}]});
  const instance=node('1:9','INSTANCE','Button',{componentProperties:{Type:{type:'VARIANT',value:'primary'}},getMainComponentAsync:async()=>component});
  const frame=node('1:2',options.nodeType||'FRAME','Home',{width:1440,height:900,children:[text,image,vector,instance],paddingLeft:24,
    layoutGrids:[{pattern:'COLUMNS',alignment:'STRETCH',gutterSize:24,count:12,visible:true,color:{r:1,g:0,b:0,a:.1},boundVariables:{gutterSize:{type:'VARIABLE_ALIAS',id:'v1'}}}]});
  const page=node('0:1','PAGE','Main',{children:[frame,set],parent:{id:'0:0',type:'DOCUMENT'}});
  for(const model of models)if(!model.parent)model.parent=[frame,set].some(proxy=>proxy.id===model.id)?page:frame;
  // This deliberately reproduces the real connector export failure.
  const exportAsync=async request=>{restCalls++;throw new Error(request.format+' export format is not supported in this context');};
  // Native inherited API functions, not enumerable raw object serialization.
  for(const model of models){model.exportAsync=exportAsync;}
  // Existing prototypes were frozen; page export is inspected only through a
  // proxy read trap to reproduce unsupported REST without exposing extra keys.
  const collection=freeze({id:'c1',key:'theme',name:'Theme',variableIds:['v1','v2','unused'],modes:[{modeId:'light',name:'Light'},{modeId:'dark',name:'Dark'}]});
  const variables=freeze({v1:{id:'v1',key:'color',name:'Foreground',variableCollectionId:'c1',resolvedType:'COLOR',valuesByMode:{light:{r:0,g:0,b:0},dark:{type:'VARIABLE_ALIAS',id:'v2'}}},
    v2:{id:'v2',key:'remote',name:'Remote gray',variableCollectionId:'c1',resolvedType:'COLOR',valuesByMode:{light:{r:.3,g:.3,b:.3},dark:{r:.5,g:.5,b:.5}}},
    unused:{id:'unused',key:'spacing',name:'Unused spacing',variableCollectionId:'c1',resolvedType:'FLOAT',valuesByMode:{light:8,dark:12}}});
  for(const model of models)freeze(model);freeze(segments);
  const before=JSON.stringify({nodes:models.map(m=>({...m,parent:m.parent?.id,children:m.children?.map(n=>n.id),fontName:m.fontName===mixed?'mixed':m.fontName})),variables,collection});
  const snapshot=()=>JSON.stringify({nodes:models.map(m=>({...m,parent:m.parent?.id,children:m.children?.map(n=>n.id),fontName:m.fontName===mixed?'mixed':m.fontName})),variables,collection});
  const api=freeze({fileKey:'FILEA',apiVersion:'1.0.0',root:{children:[page]},mixed,getNodeByIdAsync:async id=>id==='0:1'?page:null,
    setCurrentPageAsync:async target=>{assert.equal(target,page);switches++;},variables:{getLocalVariablesAsync:async()=>[variables.v1,variables.unused],
      getLocalVariableCollectionsAsync:async()=>[collection],getVariableByIdAsync:async id=>options.missingVariable&&id==='v2'?null:variables[id],
      getVariableCollectionByIdAsync:async id=>id==='c1'?collection:null}});
  return {api,page,proxies,unchanged:()=>snapshot()===before,mutations:()=>mutations,restCalls:()=>restCalls,switches:()=>switches};
}
function decode(binary,length) {
  const codes=[];for(let i=0;i<binary.length;i+=2)codes.push(binary.readUInt16BE(i));
  assert.equal(codes.shift(),256);let table=new Map(),next=257,previous=null,output=[];
  for(const code of codes){if(code===256){assert.equal(next,65536);table=new Map();next=257;previous=null;continue;}
    const entry=code<256?Buffer.from([code]):table.has(code)?table.get(code):code===next&&previous?Buffer.concat([previous,previous.subarray(0,1)]):null;
    assert(entry,'Unknown LZW code');output.push(entry);if(previous&&next<65536)table.set(next++,Buffer.concat([previous,entry.subarray(0,1)]));previous=entry;}
  const text=Buffer.concat(output).toString('ascii');assert.equal(text.length,length);return text;
}
async function collect(fixture,code=source) {
  const first=await new AsyncFunction('figma',code)(fixture.api),parts=[first];
  assert.equal(first.encoding,'lzw16-base64-json-ascii-v1');
  for(let i=1;i<first.part_count;i++)parts.push(await new AsyncFunction('figma',code.replace('const outputPart = 0;','const outputPart = '+i+';'))(fixture.api));
  for(const part of parts){assert.equal(part.receipt_sha256,first.receipt_sha256);assert.equal(part.part_count,first.part_count);assert(Buffer.byteLength(JSON.stringify(part))<20000);}
  const text=decode(Buffer.from(parts.map(part=>part.json_part).join(''),'base64'),first.receipt_length);
  assert.equal(crypto.createHash('sha256').update(text).digest('hex'),first.receipt_sha256);
  return {receipt:JSON.parse(text),parts};
}
async function test() {
  const sound=fixture(),{receipt,parts}=await collect(sound);
  assert.equal(receipt.complete,true,receipt.errors.join('\n'));assert.equal(receipt.read_only,true);assert.deepEqual(receipt.errors,[]);
  assert.equal(receipt.source_format,'figma-plugin-api-properties-v1');assert.equal(receipt.nodes.length,8);assert.equal(receipt.variables.length,6);
  const text=receipt.nodes.find(n=>n.type==='TEXT');
  assert.deepEqual(text.fonts,[{family:'Inter',style:'Regular'},{family:'Inter',style:'Semi Bold',variationSettings:{wght:600,slnt:0}}]);
  assert.deepEqual(text.visual.fontName,{__figma_mixed__:true});assert.equal(text.visual.text_segments[1].start,5);assert.equal(text.visual.text_segments[1].textWrapStyle,'AUTO');
  assert(!('fillGeometry' in text.visual));assert(!('strokeGeometry' in text.visual));assert.deepEqual(text.variables,['v1','v2']);
  assert.equal(receipt.nodes.find(n=>n.type==='FRAME').visual.paddingLeft,24);
  assert.equal(receipt.nodes.find(n=>n.type==='INSTANCE').component_ref.key,'LIBRARY');
  assert.deepEqual(receipt.nodes.find(n=>n.type==='COMPONENT').variant_properties,{Type:'primary'});
  assert.equal(receipt.nodes.flatMap(n=>n.assets).length,3);assert.equal(receipt.nodes.flatMap(n=>n.transitions).length,5);
  assert.equal(receipt.variable_sources.length,3);assert.deepEqual(receipt.document_pages,[{id:'0:1',name:'Main',type:'PAGE'}]);
  for(const node of sound.proxies){assert.deepEqual(Object.keys(node),['id']);assert.deepEqual(Object.getOwnPropertyNames(node),['id']);}
  assert.equal(sound.restCalls(),0);assert.equal(sound.mutations(),0);assert.equal(sound.switches(),parts.length);assert(sound.unchanged());
  const unsupported=fixture();
  await assert.rejects(unsupported.page.exportAsync({format:'JSON_REST_V1'}),/not supported in this context/);
  assert(unsupported.unchanged());
  // Real page receipts exceed the 65,536-code dictionary. Exercise reset with
  // production codec functions and independently decode/hash the original bytes.
  const codec=source.slice(source.indexOf('function sha256(text)'),source.indexOf('const text=JSON.stringify(receipt)'));
  let seed=0x12345678;
  const payload=Array.from({length:900000},()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return String.fromCharCode(32+((seed>>>0)%95));}).join('');
  const compressed=await new AsyncFunction('input',codec+'return {binary:lzwBase64(input),digest:sha256(input)};')(payload);
  const bytes=Buffer.from(compressed.binary,'base64');let resets=0;
  for(let i=0;i<bytes.length;i+=2)if(bytes.readUInt16BE(i)===256)resets++;
  assert(resets>1,'Expected dictionary reset');assert.equal(decode(bytes,payload.length),payload);
  assert.equal(compressed.digest,crypto.createHash('sha256').update(payload).digest('hex'));
  for(const options of [{missingVariable:true},{missingGetter:'1:2.paddingLeft'},{throwGetter:'1:7.effects'},
      {missingGetter:'1:4.variantProperties'},{badSegment:true},{paintType:'VIDEO'},{nodeType:'UNSUPPORTED_WIDGET'}]){
    const broken=fixture(options),result=(await collect(broken)).receipt;
    assert.equal(result.complete,false,JSON.stringify(options));assert(result.errors.length);assert(broken.unchanged());assert.equal(broken.mutations(),0);
  }
  const optional=(await collect(fixture({optionalBoundVariables:false}))).receipt;assert.equal(optional.complete,true,optional.errors.join('\n'));
  await assert.rejects(new AsyncFunction('figma',source.replace('await figma.setCurrentPageAsync(page);','page.name = "changed";'))(fixture().api),/mutation refused/);
  await assert.rejects(new AsyncFunction('figma',source)(Object.freeze({...fixture().api,fileKey:'OTHER'})),/Wrong Figma file/);
  await assert.rejects(new AsyncFunction('figma',source.replace('const outputPart = 0;','const outputPart = -1;'))(fixture().api),/outputPart/);
  console.log('Read-only typed collector: mixed text, ranges, geometry, aliases, variants, modes, resources, compression/hash and refusal controls passed');
}
(async()=>{
  // Fixture export drives the production collector, never another test. Python
  // validates this original receipt independently against metadata/declarations.
  if(process.argv[2]==='--fixture')process.stdout.write(JSON.stringify(await collect(fixture())));
  else await test();
})().catch(error=>{console.error(error);process.exitCode=1;});
