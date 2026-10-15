// Read-only use_figma page collector. Set file/page literals and outputPart.
// Reassemble ALL returned parts with autocode_design_sources.reassemble; a part
// is never a source receipt. Each part hashes the complete immutable JSON text.
// Repeated part reads must have exactly the same digest, identity and part count.
// Load figma-use first. This changes inspection context, never the design or its versions.
// Native nodes expose getters, not enumerable properties. This is an explicit Plugin API
// property snapshot, NOT a REST export. Unsupported getters keep the receipt incomplete,
// except the exact, retained native container capability refusals below.
const fileKey = "FILE_KEY";
const pageId = "PAGE_ID";
const outputPart = 0;
const transportEncoding = "gzip-base64-json-ascii-v1";
const sourceFormat = "figma-plugin-api-properties-v1";
const profile = {
  base: "id type name",
  scene: "visible locked componentPropertyReferences boundVariables explicitVariableModes resolvedVariableModes animationStyles animations manualKeyframeTracks timelines",
  dimensions: "x y width height minWidth maxWidth minHeight maxHeight relativeTransform absoluteTransform absoluteBoundingBox",
  layout: "absoluteRenderBounds rotation layoutSizingHorizontal layoutSizingVertical layoutAlign layoutGrow layoutPositioning gridRowAnchorIndex gridColumnAnchorIndex gridRowSpan gridColumnSpan gridChildHorizontalAlign gridChildVerticalAlign",
  blend: "opacity blendMode isMask maskType effects effectStyleId",
  fills: "fills fillStyleId",
  strokes: "strokes strokeStyleId strokeWeight strokeJoin strokeAlign dashPattern strokeGeometry",
  geometry: "strokeCap strokeMiterLimit fillGeometry",
  corners: "cornerRadius cornerSmoothing",
  rectangleCorners: "topLeftRadius topRightRadius bottomLeftRadius bottomRightRadius",
  individualStrokes: "strokeTopWeight strokeBottomWeight strokeLeftWeight strokeRightWeight",
  complexStrokes: "variableWidthStrokeProperties complexStrokeProperties",
  frame: "layoutGrids gridStyleId clipsContent guides inferredAutoLayout layoutMode paddingLeft paddingRight paddingTop paddingBottom primaryAxisSizingMode counterAxisSizingMode strokesIncludedInLayout layoutWrap primaryAxisAlignItems counterAxisAlignItems counterAxisAlignContent itemSpacing counterAxisSpacing itemReverseZIndex gridRowCount gridColumnCount gridRowGap gridColumnGap gridRowSizes gridColumnSizes gridAutoTracks gridItemsPositioning",
  prototype: "overflowDirection numberOfFixedChildren overlayPositionType overlayBackground overlayBackgroundInteraction",
  text: "hasMissingFont fontSize fontName fontWeight textCase openTypeFeatures letterSpacing hyperlink characters textAlignHorizontal textAlignVertical autoRename textStyleId",
  paragraph: "paragraphIndent paragraphSpacing textWrapStyle listSpacing hangingPunctuation hangingList textDecoration textDecorationStyle textDecorationOffset textDecorationThickness textDecorationColor textDecorationSkipInk lineHeight leadingTrim textAutoResize textTruncation maxLines",
};
const shape = ["base","scene","dimensions","layout","blend","fills","strokes","geometry","complexStrokes"];
const frame = [...shape,"corners","rectangleCorners","individualStrokes","frame"];
const types = {
  PAGE: ["base"],
  FRAME: [...frame,"prototype"], COMPONENT: [...frame,"prototype"], INSTANCE: [...frame,"prototype"],
  COMPONENT_SET: frame,
  GROUP: ["base","scene","dimensions","layout","blend"],
  TRANSFORM_GROUP: ["base","scene","dimensions","layout","blend"],
  SECTION: ["base","scene","dimensions","fills","strokes","corners","rectangleCorners"],
  SLICE: ["base","scene","dimensions","layout"],
  RECTANGLE: [...shape,"corners","rectangleCorners","individualStrokes"],
  ELLIPSE: [...shape,"corners"], LINE: shape,
  POLYGON: [...shape,"corners"], STAR: [...shape,"corners"], VECTOR: [...shape,"corners"],
  BOOLEAN_OPERATION: [...shape,"corners"], TEXT: [...shape,"text","paragraph"],
  TEXT_PATH: [...shape,"text"],
};
const extras = {
  PAGE: "backgrounds prototypeBackgrounds flowStartingPoints explicitVariableModes guides",
  FRAME: "constraints targetAspectRatio", COMPONENT: "constraints targetAspectRatio key remote",
  COMPONENT_SET: "constraints targetAspectRatio key remote", INSTANCE: "constraints targetAspectRatio componentProperties scaleFactor overrides",
  GROUP: "targetAspectRatio", TRANSFORM_GROUP: "targetAspectRatio transformModifiers",
  SECTION: "sectionContentsHidden targetAspectRatio", RECTANGLE: "constraints targetAspectRatio",
  ELLIPSE: "constraints targetAspectRatio arcData", LINE: "constraints", POLYGON: "constraints targetAspectRatio pointCount",
  STAR: "constraints targetAspectRatio pointCount innerRadius", VECTOR: "constraints targetAspectRatio vectorPaths vectorNetwork handleMirroring",
  BOOLEAN_OPERATION: "targetAspectRatio booleanOperation", TEXT: "constraints targetAspectRatio",
  TEXT_PATH: "constraints targetAspectRatio vectorPaths vectorNetwork handleMirroring textPathStartData",
};
const segmentFields = "fontSize fontName fontWeight fontStyle textDecoration textDecorationStyle textDecorationOffset textDecorationThickness textDecorationColor textDecorationSkipInk textCase lineHeight letterSpacing fills textStyleId fillStyleId listOptions listSpacing indentation paragraphIndent paragraphSpacing textWrapStyle hyperlink openTypeFeatures boundVariables textStyleOverrides".split(" ");
const nativeCapabilities = {
  COMPONENT_SET: {record:"motion_capability",fields:["animationStyles","animations","manualKeyframeTracks","timelines"]},
  SECTION: {record:"section_capability",fields:["minWidth","maxWidth","minHeight","maxHeight","strokeGeometry"]},
};
const errors = [];
let hasUnsupportedNativeCapabilities = false;
function plain(value, label, seen = new Set()) {
  if (value === figma.mixed) return {__figma_mixed__:true};
  if (value === undefined) return {__figma_undefined__:true};
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") { if (!Number.isFinite(value)) throw new Error("Nonfinite " + label); return value; }
  if (typeof value !== "object" || seen.has(value)) throw new Error("Unserializable " + label);
  seen.add(value);
  const result = Array.isArray(value) ? value.map((v,i)=>plain(v,label+"["+i+"]",seen))
    : Object.fromEntries(Object.keys(value).map(key=>[key,plain(value[key],label+"."+key,seen)]));
  seen.delete(value);
  return result;
}
function read(node, field) {
  try {
    const value = node[field];
    if (value === undefined && field !== "boundVariables") throw new Error("required getter is unavailable");
    return plain(value, node.id + "." + field);
  } catch (error) { errors.push("Unreadable " + node.id + "." + field + ": " + error.message); return {__figma_unreadable__:true}; }
}
// The native wrapper rejects these exact container getter groups despite its
// standalone typings inheriting them. Retain the actual capability errors,
// never invented empty values. Child nodes still use their own strict profiles.
function nativeCapability(node, capability) {
  const values = {}, getterErrors = {};
  for (const field of capability.fields) {
    try {
      const value=node[field];
      if (value === undefined) throw new Error("required getter is unavailable");
      values[field]=plain(value,node.id+"."+field);
    } catch(error) { getterErrors[field]=error.message; }
  }
  if (capability.fields.every(field=>getterErrors[field] === "node."+field+": no such property '"+field+"' on "+node.type+" node")) {
    hasUnsupportedNativeCapabilities=true;
    return {values,record:{version:1,status:"unsupported",getter_errors:getterErrors}};
  }
  for (const [field,message] of Object.entries(getterErrors)) {
    errors.push("Unreadable "+node.id+"."+field+": "+message);
    values[field]={__figma_unreadable__:true};
  }
  return {values};
}
function fields(type) {
  if (!types[type]) throw new Error("Unsupported Figma node property profile " + type);
  return [...new Set([...types[type].flatMap(group=>profile[group].split(" ")), ...(extras[type]||"").split(" ").filter(Boolean)])];
}
function snapshot(node) {
  const visual = {}, capability=nativeCapabilities[node.type];
  let capabilityResult;
  for (const field of fields(node.type)) {
    // Glyph contours are derived from retained text/font/range properties, not
    // authored vectors. Keep authored TEXT_PATH vectorPaths/vectorNetwork.
    if (["TEXT","TEXT_PATH"].includes(node.type) && ["fillGeometry","strokeGeometry"].includes(field)) continue;
    if (capability && capability.fields.includes(field)) {
      if (!capabilityResult) capabilityResult=nativeCapability(node,capability);
      if (capabilityResult.record) visual[capability.record]=capabilityResult.record;
      else visual[field]=capabilityResult.values[field];
      continue;
    }
    visual[field]=read(node,field);
  }
  try { visual.parent_id=node.parent ? node.parent.id : null; } catch(error) { errors.push("Unreadable "+node.id+".parent: "+error.message); }
  if (["PAGE","FRAME","COMPONENT","COMPONENT_SET","INSTANCE","GROUP","TRANSFORM_GROUP","SECTION","BOOLEAN_OPERATION"].includes(node.type)) {
    try { visual.child_ids=Array.from(node.children,node=>node.id); } catch(error) { errors.push("Unreadable "+node.id+".children: "+error.message); }
  }
  if (["FRAME","COMPONENT","INSTANCE","GROUP","TRANSFORM_GROUP","RECTANGLE","ELLIPSE","LINE","POLYGON","STAR","VECTOR","BOOLEAN_OPERATION","TEXT","TEXT_PATH"].includes(node.type)) visual.reactions=read(node,"reactions");
  if (node.type === "PAGE") {
    try { const start=node.prototypeStartNode; if (start === undefined) throw new Error("required getter is unavailable"); visual.prototype_start_node_id=start ? start.id : null; }
    catch(error) { errors.push("Unreadable "+node.id+".prototypeStartNode: "+error.message); }
  }
  if (node.type === "TEXT" || node.type === "TEXT_PATH") {
    try {
      const supported=segmentFields.filter(field=>field !== "textWrapStyle");
      const segments=node.getStyledTextSegments(supported).map(segment=>({...segment}));
      if (node.type === "TEXT") for (const segment of segments) {
        // This connector omits textWrapStyle from getStyledTextSegments; the
        // documented range getter preserves the original paragraph value.
        segment.textWrapStyle=node.getRangeTextWrapStyle(segment.start,segment.end);
      }
      visual.text_segments=plain(segments,node.id+".text_segments");
      for (const segment of visual.text_segments) for (const field of ["characters","start","end",...segmentFields])
        if ((!(field in segment) || segment[field]?.__figma_undefined__) && field !== "boundVariables" && !(field === "textWrapStyle" && node.type === "TEXT_PATH")) throw new Error("missing segment property "+field);
      let end=0;
      for (const segment of visual.text_segments) {
        if (segment.start !== end || !Number.isInteger(segment.end) || segment.end <= end || segment.characters !== visual.characters.slice(segment.start,segment.end))
          throw new Error("noncontiguous or incorrect text segment");
        end=segment.end;
      }
      if (end !== visual.characters.length) throw new Error("incomplete text segment coverage");
    } catch(error) { errors.push("Unreadable "+node.id+".text_segments: "+error.message); }
  }
  if (node.type === "COMPONENT_SET" || (node.type === "COMPONENT" && (!node.parent || node.parent.type !== "COMPONENT_SET"))) visual.componentPropertyDefinitions=read(node,"componentPropertyDefinitions");
  return visual;
}
if (figma.fileKey !== fileKey) throw new Error("Wrong Figma file");
const documentPages=figma.root.children.map(p=>({id:read(p,"id"),name:read(p,"name"),type:read(p,"type")}));
const page = await figma.getNodeByIdAsync(pageId);
if (!page || page.type !== "PAGE") throw new Error("Unreadable Figma page");
await figma.setCurrentPageAsync(page);
const nodes = [], variableIds = new Set(), collectionIds = new Set();
function aliases(value, found = variableIds) {
  if (Array.isArray(value)) { value.forEach(item => aliases(item, found)); return; }
  if (!value || typeof value !== "object") return;
  if (value.type === "VARIABLE_ALIAS") { found.add(value.id); variableIds.add(value.id); }
  if (value.variableId) { found.add(value.variableId); variableIds.add(value.variableId); }
  if (value.variableCollectionId) collectionIds.add(value.variableCollectionId);
  Object.values(value).forEach(item => aliases(item, found));
}
function assetsFrom(visual,nodeId) {
  const found = [];
  const paints = [["fills",visual.fills],["strokes",visual.strokes],["backgrounds",visual.backgrounds],["prototypeBackgrounds",visual.prototypeBackgrounds],
    ...(visual.text_segments || []).map((segment,index)=>["text_segments:"+index+":fills",segment.fills])];
  for (const [field,values] of paints) {
    if (Array.isArray(values)) values.forEach((paint,index)=>{
      if (paint.type === "IMAGE") {
        if (!paint.imageHash) errors.push("Unreadable image paint "+nodeId+"."+field+"["+index+"]");
        else found.push({id:nodeId+":"+field+":"+index+":"+paint.imageHash,mime_type:"image/png"});
      }
      if (!["SOLID","GRADIENT_LINEAR","GRADIENT_RADIAL","GRADIENT_ANGULAR","GRADIENT_DIAMOND","IMAGE"].includes(paint.type)) errors.push("Unsupported required paint asset "+nodeId+"."+field+"["+index+"]: "+paint.type);
    });
  }
  if (["VECTOR","BOOLEAN_OPERATION","TEXT_PATH"].includes(visual.type)) found.push({id:nodeId+":svg",mime_type:"image/svg+xml"});
  return found;
}
async function visit(node) {
  let visual;
  try { visual=snapshot(node); } catch(error) { errors.push("Unreadable "+node.id+": "+error.message); return; }
  const item = {id:node.id,type:node.type,name:node.name,fonts:[],assets:assetsFrom(visual,node.id),variables:[],transitions:[],visual};
  if ("width" in visual) { item.width=visual.width; item.height=visual.height; }
  if (node.type === "TEXT" || node.type === "TEXT_PATH") {
    const segments=visual.text_segments || [];
    const names=segments.map(segment=>segment.fontName);
    if (!names.length && visual.characters === "") names.push(visual.fontName);
    item.fonts=Array.from(new Map(names.map(font=>[JSON.stringify(font),font])).values());
  }
  const localAliases = new Set(); aliases(visual,localAliases);
  for (const modes of [visual.explicitVariableModes,visual.resolvedVariableModes])
    if (modes && typeof modes === "object") for (const id of Object.keys(modes)) collectionIds.add(id);
  for (const [index,reaction] of (Array.isArray(visual.reactions)?visual.reactions:[]).entries()) {
    const actions=reaction.actions || (reaction.action ? [reaction.action] : []);
    actions.forEach((action,actionIndex)=>item.transitions.push({id:node.id+":reaction:"+index+":"+actionIndex,
      target_node_id:action.destinationId||"",trigger:JSON.stringify(reaction.trigger),action}));
  }
  item.variables=Array.from(localAliases).sort();
  if (node.type === "COMPONENT" || node.type === "COMPONENT_SET") {
    item.component_key=visual.key;
    if (node.type === "COMPONENT") {
      if (node.parent && node.parent.type === "COMPONENT_SET") {
        // The native wrapper may expose this read getter despite omitting it from
        // its standalone typings. Absence is a blocker; never guess from the name.
        const value=read(node,"variantProperties");
        visual.variantProperties=value; item.variant_properties=value;
        if (!value || Array.isArray(value) || typeof value !== "object" || !Object.keys(value).length)
          errors.push("Unreadable variant identity "+node.id);
      } else item.variant_properties={}; // Standalone component has no variant coordinates.
    }
  }
  if (node.type === "INSTANCE") {
    try {
      const main=await node.getMainComponentAsync();
      if (!main) throw new Error("main component unavailable");
      const owner=main.parent && main.parent.type === "COMPONENT_SET" ? main.parent : main;
      const variants=Object.fromEntries(Object.entries(visual.componentProperties).filter(([,value])=>value.type === "VARIANT").map(([key,value])=>[key,value.value]));
      item.component_ref={key:read(owner,"key"),node_id:read(main,"id"),variant_properties:variants,remote:read(main,"remote")};
      visual.main_component=item.component_ref;
      visual.main_component_owner={id:read(owner,"id"),type:read(owner,"type"),componentPropertyDefinitions:read(owner,"componentPropertyDefinitions")};
      aliases(visual.main_component_owner,localAliases);
      item.variables=Array.from(localAliases).sort();
    } catch(error) { errors.push("Unreadable instance component "+node.id+": "+error.message); }
  }
  nodes.push(item);
  if (visual.child_ids) {
    try { for (const child of node.children) await visit(child); } catch(error) { errors.push("Unreadable children "+node.id+": "+error.message); }
  }
}
await visit(page);
// Include unused local tokens/modes and all recursively referenced remote aliases.
// Retain the original collection/mode roster so downstream validation can check
// completeness independently of the derived manifest token rows.
const localVariables=await figma.variables.getLocalVariablesAsync();
const localCollections=await figma.variables.getLocalVariableCollectionsAsync();
for (const variable of localVariables) variableIds.add(variable.id);
for (const collection of localCollections) collectionIds.add(collection.id);
const collections=new Map(), sources=new Map();
async function collectionFor(id) {
  if (collections.has(id)) return collections.get(id);
  let source;
  try { source=await figma.variables.getVariableCollectionByIdAsync(id); }
  catch(error) { errors.push("Unreadable variable collection "+id+": "+error.message); return null; }
  if (!source) { errors.push("Unreadable variable collection "+id); return null; }
  const row={};
  for (const field of ["id","key","name","modes","variableIds"]) row[field]=read(source,field);
  collections.set(id,row);
  if (Array.isArray(row.variableIds)) for (const variableId of row.variableIds) variableIds.add(variableId);
  return row;
}
for (const id of collectionIds) await collectionFor(id);
const variables=[];
for (const id of variableIds) {
  if (sources.has(id)) continue;
  let source;
  try { source=await figma.variables.getVariableByIdAsync(id); }
  catch(error) { errors.push("Unreadable variable "+id+": "+error.message); continue; }
  if (!source) { errors.push("Unreadable variable "+id); continue; }
  const v={};
  for (const field of ["id","key","name","resolvedType","variableCollectionId","valuesByMode"]) v[field]=read(source,field);
  sources.set(id,v);
  const collection=await collectionFor(v.variableCollectionId);
  if (!collection || !Array.isArray(collection.modes)) continue;
  for (const mode of collection.modes) {
    if (!(mode.modeId in v.valuesByMode)) { errors.push("Missing variable mode "+id+":"+mode.modeId); continue; }
    const value=v.valuesByMode[mode.modeId]; aliases(value);
    variables.push({key:v.key+":"+mode.modeId,source_id:v.id,name:v.name,kind:v.resolvedType,value,
      collection:collection.name,mode:mode.name,mode_id:mode.modeId});
  }
}
if (figma.apiVersion !== "1.0.0") errors.push("Unreadable or unsupported Plugin API version "+String(figma.apiVersion));
const receipt={version:1,source_format:sourceFormat,property_profile:hasUnsupportedNativeCapabilities ? "autocode-design-properties-v2" : "autocode-design-properties-v1",api_version:figma.apiVersion,
  file_key:fileKey,page_id:pageId,document_pages:documentPages,read_only:true,complete:errors.length === 0,errors,nodes,variables,
  local_variable_ids:localVariables.map(v=>v.id),local_collection_ids:localCollections.map(c=>c.id),
  variable_sources:Array.from(sources.values()),variable_collections:Array.from(collections.values())};

// ASCII JSON avoids split surrogate pairs and gives the host an exact UTF-8 hash.
// Bounded parts stay below the connector's 20KB response limit even after JSON
// string escaping. No cache, file write, mutation or skipped source is involved.
function sha256(text) {
  const k=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const h=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const length=text.length, bytes=Array.from(text,c=>c.charCodeAt(0));bytes.push(128);
  while (bytes.length%64 !== 56) bytes.push(0);
  const bits=length*8;
  for (let i=7;i>=0;i--) bytes.push(i>=4 ? Math.floor(bits/2**(i*8))&255 : (bits>>>i*8)&255);
  const r=(value,n)=>(value>>>n)|(value<<(32-n));
  for (let offset=0;offset<bytes.length;offset+=64) {
    const w=Array(64);for(let i=0;i<16;i++)w[i]=(bytes[offset+i*4]<<24)|(bytes[offset+i*4+1]<<16)|(bytes[offset+i*4+2]<<8)|bytes[offset+i*4+3];
    for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(r(a,7)^r(a,18)^(a>>>3))+w[i-7]+(r(b,17)^r(b,19)^(b>>>10)))|0;}
    let [a,b,c,d,e,f,g,j]=h;
    for(let i=0;i<64;i++){const t=(j+(r(e,6)^r(e,11)^r(e,25))+((e&f)^(~e&g))+k[i]+w[i])|0;const u=((r(a,2)^r(a,13)^r(a,22))+((a&b)^(a&c)^(b&c)))|0;j=g;g=f;f=e;e=(d+t)|0;d=c;c=b;b=a;a=(t+u)|0;}
    for(const [i,value] of [a,b,c,d,e,f,g,j].entries())h[i]=(h[i]+value)|0;
  }
  return h.map(value=>(value>>>0).toString(16).padStart(8,"0")).join("");
}
// BEGIN PINNED FFLATE GZIP VENDOR
// Synchronous gzip-only fflate 0.8.2 vendor bundle.
// Upstream: https://github.com/101arrowz/fflate/tree/v0.8.2
// npm integrity: sha512-cPJU47OaAoCbg0pBvzsgpTPhmhqI5eJjh/JIu8tPj5q+T7iLvW/JAYUqmE7KOB4R1ZyEhzBaIQpQpardBF5z8A==
// esm/browser.js SHA256: 8cc1f687e0159e977addb6b85e274dbd11e622cf151f4fcb7b85d49622ea43e7
// Generated with esbuild 0.25.10; pinned options in README.md.
/*
MIT License

Copyright (c) 2023 Arjun Barrett

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.*/
var autocodeFigmaGzip = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // entry.js
  var entry_exports = {};
  __export(entry_exports, {
    asciiGzip: () => asciiGzip
  });

  // node_modules/fflate/esm/browser.js
  var u8 = Uint8Array;
  var u16 = Uint16Array;
  var i32 = Int32Array;
  var fleb = new u8([
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    1,
    1,
    1,
    2,
    2,
    2,
    2,
    3,
    3,
    3,
    3,
    4,
    4,
    4,
    4,
    5,
    5,
    5,
    5,
    0,
    /* unused */
    0,
    0,
    /* impossible */
    0
  ]);
  var fdeb = new u8([
    0,
    0,
    0,
    0,
    1,
    1,
    2,
    2,
    3,
    3,
    4,
    4,
    5,
    5,
    6,
    6,
    7,
    7,
    8,
    8,
    9,
    9,
    10,
    10,
    11,
    11,
    12,
    12,
    13,
    13,
    /* unused */
    0,
    0
  ]);
  var clim = new u8([16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]);
  var freb = function(eb, start) {
    var b = new u16(31);
    for (var i = 0; i < 31; ++i) {
      b[i] = start += 1 << eb[i - 1];
    }
    var r = new i32(b[30]);
    for (var i = 1; i < 30; ++i) {
      for (var j = b[i]; j < b[i + 1]; ++j) {
        r[j] = j - b[i] << 5 | i;
      }
    }
    return { b, r };
  };
  var _a = freb(fleb, 2);
  var fl = _a.b;
  var revfl = _a.r;
  fl[28] = 258, revfl[258] = 28;
  var _b = freb(fdeb, 0);
  var fd = _b.b;
  var revfd = _b.r;
  var rev = new u16(32768);
  for (i = 0; i < 32768; ++i) {
    x = (i & 43690) >> 1 | (i & 21845) << 1;
    x = (x & 52428) >> 2 | (x & 13107) << 2;
    x = (x & 61680) >> 4 | (x & 3855) << 4;
    rev[i] = ((x & 65280) >> 8 | (x & 255) << 8) >> 1;
  }
  var x;
  var i;
  var hMap = (function(cd, mb, r) {
    var s = cd.length;
    var i = 0;
    var l = new u16(mb);
    for (; i < s; ++i) {
      if (cd[i])
        ++l[cd[i] - 1];
    }
    var le = new u16(mb);
    for (i = 1; i < mb; ++i) {
      le[i] = le[i - 1] + l[i - 1] << 1;
    }
    var co;
    if (r) {
      co = new u16(1 << mb);
      var rvb = 15 - mb;
      for (i = 0; i < s; ++i) {
        if (cd[i]) {
          var sv = i << 4 | cd[i];
          var r_1 = mb - cd[i];
          var v = le[cd[i] - 1]++ << r_1;
          for (var m = v | (1 << r_1) - 1; v <= m; ++v) {
            co[rev[v] >> rvb] = sv;
          }
        }
      }
    } else {
      co = new u16(s);
      for (i = 0; i < s; ++i) {
        if (cd[i]) {
          co[i] = rev[le[cd[i] - 1]++] >> 15 - cd[i];
        }
      }
    }
    return co;
  });
  var flt = new u8(288);
  for (i = 0; i < 144; ++i)
    flt[i] = 8;
  var i;
  for (i = 144; i < 256; ++i)
    flt[i] = 9;
  var i;
  for (i = 256; i < 280; ++i)
    flt[i] = 7;
  var i;
  for (i = 280; i < 288; ++i)
    flt[i] = 8;
  var i;
  var fdt = new u8(32);
  for (i = 0; i < 32; ++i)
    fdt[i] = 5;
  var i;
  var flm = /* @__PURE__ */ hMap(flt, 9, 0);
  var fdm = /* @__PURE__ */ hMap(fdt, 5, 0);
  var shft = function(p) {
    return (p + 7) / 8 | 0;
  };
  var slc = function(v, s, e) {
    if (s == null || s < 0)
      s = 0;
    if (e == null || e > v.length)
      e = v.length;
    return new u8(v.subarray(s, e));
  };
  var wbits = function(d, p, v) {
    v <<= p & 7;
    var o = p / 8 | 0;
    d[o] |= v;
    d[o + 1] |= v >> 8;
  };
  var wbits16 = function(d, p, v) {
    v <<= p & 7;
    var o = p / 8 | 0;
    d[o] |= v;
    d[o + 1] |= v >> 8;
    d[o + 2] |= v >> 16;
  };
  var hTree = function(d, mb) {
    var t = [];
    for (var i = 0; i < d.length; ++i) {
      if (d[i])
        t.push({ s: i, f: d[i] });
    }
    var s = t.length;
    var t2 = t.slice();
    if (!s)
      return { t: et, l: 0 };
    if (s == 1) {
      var v = new u8(t[0].s + 1);
      v[t[0].s] = 1;
      return { t: v, l: 1 };
    }
    t.sort(function(a, b) {
      return a.f - b.f;
    });
    t.push({ s: -1, f: 25001 });
    var l = t[0], r = t[1], i0 = 0, i1 = 1, i2 = 2;
    t[0] = { s: -1, f: l.f + r.f, l, r };
    while (i1 != s - 1) {
      l = t[t[i0].f < t[i2].f ? i0++ : i2++];
      r = t[i0 != i1 && t[i0].f < t[i2].f ? i0++ : i2++];
      t[i1++] = { s: -1, f: l.f + r.f, l, r };
    }
    var maxSym = t2[0].s;
    for (var i = 1; i < s; ++i) {
      if (t2[i].s > maxSym)
        maxSym = t2[i].s;
    }
    var tr = new u16(maxSym + 1);
    var mbt = ln(t[i1 - 1], tr, 0);
    if (mbt > mb) {
      var i = 0, dt = 0;
      var lft = mbt - mb, cst = 1 << lft;
      t2.sort(function(a, b) {
        return tr[b.s] - tr[a.s] || a.f - b.f;
      });
      for (; i < s; ++i) {
        var i2_1 = t2[i].s;
        if (tr[i2_1] > mb) {
          dt += cst - (1 << mbt - tr[i2_1]);
          tr[i2_1] = mb;
        } else
          break;
      }
      dt >>= lft;
      while (dt > 0) {
        var i2_2 = t2[i].s;
        if (tr[i2_2] < mb)
          dt -= 1 << mb - tr[i2_2]++ - 1;
        else
          ++i;
      }
      for (; i >= 0 && dt; --i) {
        var i2_3 = t2[i].s;
        if (tr[i2_3] == mb) {
          --tr[i2_3];
          ++dt;
        }
      }
      mbt = mb;
    }
    return { t: new u8(tr), l: mbt };
  };
  var ln = function(n, l, d) {
    return n.s == -1 ? Math.max(ln(n.l, l, d + 1), ln(n.r, l, d + 1)) : l[n.s] = d;
  };
  var lc = function(c) {
    var s = c.length;
    while (s && !c[--s])
      ;
    var cl = new u16(++s);
    var cli = 0, cln = c[0], cls = 1;
    var w = function(v) {
      cl[cli++] = v;
    };
    for (var i = 1; i <= s; ++i) {
      if (c[i] == cln && i != s)
        ++cls;
      else {
        if (!cln && cls > 2) {
          for (; cls > 138; cls -= 138)
            w(32754);
          if (cls > 2) {
            w(cls > 10 ? cls - 11 << 5 | 28690 : cls - 3 << 5 | 12305);
            cls = 0;
          }
        } else if (cls > 3) {
          w(cln), --cls;
          for (; cls > 6; cls -= 6)
            w(8304);
          if (cls > 2)
            w(cls - 3 << 5 | 8208), cls = 0;
        }
        while (cls--)
          w(cln);
        cls = 1;
        cln = c[i];
      }
    }
    return { c: cl.subarray(0, cli), n: s };
  };
  var clen = function(cf, cl) {
    var l = 0;
    for (var i = 0; i < cl.length; ++i)
      l += cf[i] * cl[i];
    return l;
  };
  var wfblk = function(out, pos, dat) {
    var s = dat.length;
    var o = shft(pos + 2);
    out[o] = s & 255;
    out[o + 1] = s >> 8;
    out[o + 2] = out[o] ^ 255;
    out[o + 3] = out[o + 1] ^ 255;
    for (var i = 0; i < s; ++i)
      out[o + i + 4] = dat[i];
    return (o + 4 + s) * 8;
  };
  var wblk = function(dat, out, final, syms, lf, df, eb, li, bs, bl, p) {
    wbits(out, p++, final);
    ++lf[256];
    var _a2 = hTree(lf, 15), dlt = _a2.t, mlb = _a2.l;
    var _b2 = hTree(df, 15), ddt = _b2.t, mdb = _b2.l;
    var _c = lc(dlt), lclt = _c.c, nlc = _c.n;
    var _d = lc(ddt), lcdt = _d.c, ndc = _d.n;
    var lcfreq = new u16(19);
    for (var i = 0; i < lclt.length; ++i)
      ++lcfreq[lclt[i] & 31];
    for (var i = 0; i < lcdt.length; ++i)
      ++lcfreq[lcdt[i] & 31];
    var _e = hTree(lcfreq, 7), lct = _e.t, mlcb = _e.l;
    var nlcc = 19;
    for (; nlcc > 4 && !lct[clim[nlcc - 1]]; --nlcc)
      ;
    var flen = bl + 5 << 3;
    var ftlen = clen(lf, flt) + clen(df, fdt) + eb;
    var dtlen = clen(lf, dlt) + clen(df, ddt) + eb + 14 + 3 * nlcc + clen(lcfreq, lct) + 2 * lcfreq[16] + 3 * lcfreq[17] + 7 * lcfreq[18];
    if (bs >= 0 && flen <= ftlen && flen <= dtlen)
      return wfblk(out, p, dat.subarray(bs, bs + bl));
    var lm, ll, dm, dl;
    wbits(out, p, 1 + (dtlen < ftlen)), p += 2;
    if (dtlen < ftlen) {
      lm = hMap(dlt, mlb, 0), ll = dlt, dm = hMap(ddt, mdb, 0), dl = ddt;
      var llm = hMap(lct, mlcb, 0);
      wbits(out, p, nlc - 257);
      wbits(out, p + 5, ndc - 1);
      wbits(out, p + 10, nlcc - 4);
      p += 14;
      for (var i = 0; i < nlcc; ++i)
        wbits(out, p + 3 * i, lct[clim[i]]);
      p += 3 * nlcc;
      var lcts = [lclt, lcdt];
      for (var it = 0; it < 2; ++it) {
        var clct = lcts[it];
        for (var i = 0; i < clct.length; ++i) {
          var len = clct[i] & 31;
          wbits(out, p, llm[len]), p += lct[len];
          if (len > 15)
            wbits(out, p, clct[i] >> 5 & 127), p += clct[i] >> 12;
        }
      }
    } else {
      lm = flm, ll = flt, dm = fdm, dl = fdt;
    }
    for (var i = 0; i < li; ++i) {
      var sym = syms[i];
      if (sym > 255) {
        var len = sym >> 18 & 31;
        wbits16(out, p, lm[len + 257]), p += ll[len + 257];
        if (len > 7)
          wbits(out, p, sym >> 23 & 31), p += fleb[len];
        var dst = sym & 31;
        wbits16(out, p, dm[dst]), p += dl[dst];
        if (dst > 3)
          wbits16(out, p, sym >> 5 & 8191), p += fdeb[dst];
      } else {
        wbits16(out, p, lm[sym]), p += ll[sym];
      }
    }
    wbits16(out, p, lm[256]);
    return p + ll[256];
  };
  var deo = /* @__PURE__ */ new i32([65540, 131080, 131088, 131104, 262176, 1048704, 1048832, 2114560, 2117632]);
  var et = /* @__PURE__ */ new u8(0);
  var dflt = function(dat, lvl, plvl, pre, post, st) {
    var s = st.z || dat.length;
    var o = new u8(pre + s + 5 * (1 + Math.ceil(s / 7e3)) + post);
    var w = o.subarray(pre, o.length - post);
    var lst = st.l;
    var pos = (st.r || 0) & 7;
    if (lvl) {
      if (pos)
        w[0] = st.r >> 3;
      var opt = deo[lvl - 1];
      var n = opt >> 13, c = opt & 8191;
      var msk_1 = (1 << plvl) - 1;
      var prev = st.p || new u16(32768), head = st.h || new u16(msk_1 + 1);
      var bs1_1 = Math.ceil(plvl / 3), bs2_1 = 2 * bs1_1;
      var hsh = function(i2) {
        return (dat[i2] ^ dat[i2 + 1] << bs1_1 ^ dat[i2 + 2] << bs2_1) & msk_1;
      };
      var syms = new i32(25e3);
      var lf = new u16(288), df = new u16(32);
      var lc_1 = 0, eb = 0, i = st.i || 0, li = 0, wi = st.w || 0, bs = 0;
      for (; i + 2 < s; ++i) {
        var hv = hsh(i);
        var imod = i & 32767, pimod = head[hv];
        prev[imod] = pimod;
        head[hv] = imod;
        if (wi <= i) {
          var rem = s - i;
          if ((lc_1 > 7e3 || li > 24576) && (rem > 423 || !lst)) {
            pos = wblk(dat, w, 0, syms, lf, df, eb, li, bs, i - bs, pos);
            li = lc_1 = eb = 0, bs = i;
            for (var j = 0; j < 286; ++j)
              lf[j] = 0;
            for (var j = 0; j < 30; ++j)
              df[j] = 0;
          }
          var l = 2, d = 0, ch_1 = c, dif = imod - pimod & 32767;
          if (rem > 2 && hv == hsh(i - dif)) {
            var maxn = Math.min(n, rem) - 1;
            var maxd = Math.min(32767, i);
            var ml = Math.min(258, rem);
            while (dif <= maxd && --ch_1 && imod != pimod) {
              if (dat[i + l] == dat[i + l - dif]) {
                var nl = 0;
                for (; nl < ml && dat[i + nl] == dat[i + nl - dif]; ++nl)
                  ;
                if (nl > l) {
                  l = nl, d = dif;
                  if (nl > maxn)
                    break;
                  var mmd = Math.min(dif, nl - 2);
                  var md = 0;
                  for (var j = 0; j < mmd; ++j) {
                    var ti = i - dif + j & 32767;
                    var pti = prev[ti];
                    var cd = ti - pti & 32767;
                    if (cd > md)
                      md = cd, pimod = ti;
                  }
                }
              }
              imod = pimod, pimod = prev[imod];
              dif += imod - pimod & 32767;
            }
          }
          if (d) {
            syms[li++] = 268435456 | revfl[l] << 18 | revfd[d];
            var lin = revfl[l] & 31, din = revfd[d] & 31;
            eb += fleb[lin] + fdeb[din];
            ++lf[257 + lin];
            ++df[din];
            wi = i + l;
            ++lc_1;
          } else {
            syms[li++] = dat[i];
            ++lf[dat[i]];
          }
        }
      }
      for (i = Math.max(i, wi); i < s; ++i) {
        syms[li++] = dat[i];
        ++lf[dat[i]];
      }
      pos = wblk(dat, w, lst, syms, lf, df, eb, li, bs, i - bs, pos);
      if (!lst) {
        st.r = pos & 7 | w[pos / 8 | 0] << 3;
        pos -= 7;
        st.h = head, st.p = prev, st.i = i, st.w = wi;
      }
    } else {
      for (var i = st.w || 0; i < s + lst; i += 65535) {
        var e = i + 65535;
        if (e >= s) {
          w[pos / 8 | 0] = lst;
          e = s;
        }
        pos = wfblk(w, pos + 1, dat.subarray(i, e));
      }
      st.i = s;
    }
    return slc(o, 0, pre + shft(pos) + post);
  };
  var crct = /* @__PURE__ */ (function() {
    var t = new Int32Array(256);
    for (var i = 0; i < 256; ++i) {
      var c = i, k = 9;
      while (--k)
        c = (c & 1 && -306674912) ^ c >>> 1;
      t[i] = c;
    }
    return t;
  })();
  var crc = function() {
    var c = -1;
    return {
      p: function(d) {
        var cr = c;
        for (var i = 0; i < d.length; ++i)
          cr = crct[cr & 255 ^ d[i]] ^ cr >>> 8;
        c = cr;
      },
      d: function() {
        return ~c;
      }
    };
  };
  var dopt = function(dat, opt, pre, post, st) {
    if (!st) {
      st = { l: 1 };
      if (opt.dictionary) {
        var dict = opt.dictionary.subarray(-32768);
        var newDat = new u8(dict.length + dat.length);
        newDat.set(dict);
        newDat.set(dat, dict.length);
        dat = newDat;
        st.w = dict.length;
      }
    }
    return dflt(dat, opt.level == null ? 6 : opt.level, opt.mem == null ? st.l ? Math.ceil(Math.max(8, Math.min(13, Math.log(dat.length))) * 1.5) : 20 : 12 + opt.mem, pre, post, st);
  };
  var wbytes = function(d, b, v) {
    for (; v; ++b)
      d[b] = v, v >>>= 8;
  };
  var gzh = function(c, o) {
    var fn = o.filename;
    c[0] = 31, c[1] = 139, c[2] = 8, c[8] = o.level < 2 ? 4 : o.level == 9 ? 2 : 0, c[9] = 3;
    if (o.mtime != 0)
      wbytes(c, 4, Math.floor(new Date(o.mtime || Date.now()) / 1e3));
    if (fn) {
      c[3] = 8;
      for (var i = 0; i <= fn.length; ++i)
        c[i + 10] = fn.charCodeAt(i);
    }
  };
  var gzhl = function(o) {
    return 10 + (o.filename ? o.filename.length + 1 : 0);
  };
  function gzipSync(data, opts) {
    if (!opts)
      opts = {};
    var c = crc(), l = data.length;
    c.p(data);
    var d = dopt(data, opts, gzhl(opts), 8), s = d.length;
    return gzh(d, opts), wbytes(d, s - 8, c.d()), wbytes(d, s - 4, l), d;
  }
  var td = false;
  var tds = 0;
  try {
    td.decode(et, { stream: true });
    tds = 1;
  } catch (e) {
  }

  // entry.js
  function asciiGzip(bytes) {
    return gzipSync(bytes, { level: 9, mtime: 0 });
  }
  return __toCommonJS(entry_exports);
})();
// END PINNED FFLATE GZIP VENDOR

function gzipBase64(text) {
  const bytes=new Uint8Array(text.length);
  for (let i=0;i<text.length;i++) bytes[i]=text.charCodeAt(i);
  return binaryBase64(autocodeFigmaGzip.asciiGzip(bytes));
}
function binaryBase64(bytes) {
  const alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let encoded="";
  for(let i=0;i<bytes.length;i+=3){const a=bytes[i],b=bytes[i+1],c=bytes[i+2];encoded+=alphabet[a>>>2]+alphabet[((a&3)<<4)|((b||0)>>>4)]+(b===undefined?"=":alphabet[((b&15)<<2)|((c||0)>>>6)])+(c===undefined?"=":alphabet[c&63]);}
  return encoded;
}

// Standard LZW with 16-bit big-endian codes and explicit dictionary resets.
// The host decodes, bounds and re-encodes it independently before accepting SHA.
function lzwBase64(text) {
  const bytes=[], dict=new Map();let next=257, prefix=text.charCodeAt(0);
  const emit=code=>{bytes.push(code>>>8,code&255);};emit(256);
  for (let i=1;i<text.length;i++) {
    const c=text.charCodeAt(i), key=prefix+":"+c;
    if (dict.has(key)) prefix=dict.get(key);
    else {emit(prefix);if(next<65536)dict.set(key,next++);else{emit(256);dict.clear();next=257;}prefix=c;}
  }
  emit(prefix);
  return binaryBase64(bytes);
}
const text=JSON.stringify(receipt).replace(/[^\x00-\x7f]/g,c=>"\\u"+c.charCodeAt(0).toString(16).padStart(4,"0"));
if(text.length>33554432)throw new Error("Figma source receipt exceeds the 32MiB transport bound");
if (!["gzip-base64-json-ascii-v1","lzw16-base64-json-ascii-v1"].includes(transportEncoding)) throw new Error("Unsupported source transport encoding");
const encoded=transportEncoding === "gzip-base64-json-ascii-v1" ? gzipBase64(text) : lzwBase64(text),size=14000,count=Math.ceil(encoded.length/size);
if (!Number.isInteger(outputPart) || outputPart<0 || outputPart>=count) throw new Error("Invalid source receipt outputPart");
return {transport_version:1,encoding:transportEncoding,source_format:sourceFormat,file_key:fileKey,page_id:pageId,
  receipt_sha256:sha256(text),receipt_length:text.length,encoded_length:encoded.length,part_index:outputPart,part_count:count,
  complete:receipt.complete,error_count:errors.length,json_part:encoded.slice(outputPart*size,(outputPart+1)*size)};
