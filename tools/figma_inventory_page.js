// Read-only use_figma page collector. Set file/page literals and outputPart.
// Reassemble ALL returned parts with autocode_design_sources.reassemble; a part
// is never a source receipt. Each part hashes the complete immutable JSON text.
// Repeated part reads must have exactly the same digest, identity and part count.
// Load figma-use first. This changes inspection context, never the design or its versions.
// Native nodes expose getters, not enumerable properties. This is an explicit Plugin API
// property snapshot, NOT a REST export. Unsupported getters keep the receipt incomplete.
const fileKey = "FILE_KEY";
const pageId = "PAGE_ID";
const outputPart = 0;
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
const errors = [];
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
function fields(type) {
  if (!types[type]) throw new Error("Unsupported Figma node property profile " + type);
  return [...new Set([...types[type].flatMap(group=>profile[group].split(" ")), ...(extras[type]||"").split(" ").filter(Boolean)])];
}
function snapshot(node) {
  const visual = {};
  for (const field of fields(node.type)) {
    // Glyph contours are derived from retained text/font/range properties, not
    // authored vectors. Keep authored TEXT_PATH vectorPaths/vectorNetwork.
    if (["TEXT","TEXT_PATH"].includes(node.type) && ["fillGeometry","strokeGeometry"].includes(field)) continue;
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
const receipt={version:1,source_format:sourceFormat,property_profile:"autocode-design-properties-v1",api_version:figma.apiVersion,
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
  const alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let encoded="";
  for(let i=0;i<bytes.length;i+=3){const a=bytes[i],b=bytes[i+1],c=bytes[i+2];encoded+=alphabet[a>>>2]+alphabet[((a&3)<<4)|((b||0)>>>4)]+(b===undefined?"=":alphabet[((b&15)<<2)|((c||0)>>>6)])+(c===undefined?"=":alphabet[c&63]);}
  return encoded;
}
const text=JSON.stringify(receipt).replace(/[^\x00-\x7f]/g,c=>"\\u"+c.charCodeAt(0).toString(16).padStart(4,"0"));
if(text.length>33554432)throw new Error("Figma source receipt exceeds the 32MiB transport bound");
const encoded=lzwBase64(text),size=14000,count=Math.ceil(encoded.length/size);
if (!Number.isInteger(outputPart) || outputPart<0 || outputPart>=count) throw new Error("Invalid source receipt outputPart");
return {transport_version:1,encoding:"lzw16-base64-json-ascii-v1",source_format:sourceFormat,file_key:fileKey,page_id:pageId,
  receipt_sha256:sha256(text),receipt_length:text.length,encoded_length:encoded.length,part_index:outputPart,part_count:count,
  complete:receipt.complete,error_count:errors.length,json_part:encoded.slice(outputPart*size,(outputPart+1)*size)};
