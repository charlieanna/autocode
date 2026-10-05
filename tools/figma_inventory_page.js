// Read-only use_figma page collector. Replace only the two literals; run once per page.
// Load figma-use first. This changes inspection context, never the design or its versions.
const fileKey = "FILE_KEY";
const pageId = "PAGE_ID";
if (figma.fileKey !== fileKey) throw new Error("Wrong Figma file");
const page = await figma.getNodeByIdAsync(pageId);
if (!page || page.type !== "PAGE") throw new Error("Unreadable Figma page");
await figma.setCurrentPageAsync(page);
const rest = await page.exportAsync({format:"JSON_REST_V1"});
const originals = new Map();
function index(node) {
  const {children, ...properties} = node;
  originals.set(node.id, properties);
  (children || []).forEach(index);
}
if (!rest.document) throw new Error("Unreadable original page properties");
index(rest.document);
const nodes = [], variableIds = new Set(), collectionIds = new Set(), errors = [];
function aliases(value, found = variableIds) {
  if (Array.isArray(value)) { value.forEach(item => aliases(item, found)); return; }
  if (!value || typeof value !== "object") return;
  if (value.type === "VARIABLE_ALIAS") { found.add(value.id); variableIds.add(value.id); }
  if (value.variableId) { found.add(value.variableId); variableIds.add(value.variableId); }
  if (value.variableCollectionId) collectionIds.add(value.variableCollectionId);
  Object.values(value).forEach(item => aliases(item, found));
}
async function visit(node) {
  const item = {id:node.id, type:node.type, name:node.name, fonts:[], assets:[], variables:[], transitions:[], visual:originals.get(node.id)};
  if (!item.visual) errors.push("Missing original node properties " + node.id);
  if ("width" in node) { item.width=node.width; item.height=node.height; }
  if (node.type === "TEXT") {
    const names = node.getStyledTextSegments(["fontName"]).map(s => s.fontName);
    if (!names.length && node.fontName !== figma.mixed) names.push(node.fontName);
    item.fonts = Array.from(new Map(names.map(f => [JSON.stringify(f),f])).values());
  }
  const localAliases = new Set();
  if ("boundVariables" in node) aliases(node.boundVariables, localAliases);
  for (const field of ["fills","strokes"]) {
    if (field in node && Array.isArray(node[field])) node[field].forEach((paint,index) => {
      aliases(paint.boundVariables, localAliases);
      if (paint.type === "IMAGE") item.assets.push({id:node.id+":"+field+":"+index+":"+paint.imageHash,mime_type:"image/png"});
    });
  }
  if (["VECTOR","BOOLEAN_OPERATION"].includes(node.type)) item.assets.push({id:node.id+":svg",mime_type:"image/svg+xml"});
  if ("reactions" in node) node.reactions.forEach((reaction,index) => {
    const actions = reaction.actions || (reaction.action ? [reaction.action] : []);
    actions.forEach((action,actionIndex) => item.transitions.push({
      id:node.id+":reaction:"+index+":"+actionIndex,
      target_node_id:action.destinationId || "", trigger:JSON.stringify(reaction.trigger), action}));
    aliases(actions, localAliases);
  });
  item.variables = Array.from(localAliases).sort();
  if (node.type === "COMPONENT" || node.type === "COMPONENT_SET") {
    item.component_key=node.key;
    if (node.type === "COMPONENT") item.variant_properties=node.variantProperties || {};
  }
  if (node.type === "INSTANCE") {
    const main = await node.getMainComponentAsync();
    if (!main) errors.push("Unreadable instance component " + node.id);
    else item.component_ref = {key:main.parent && main.parent.type === "COMPONENT_SET" ? main.parent.key : main.key,
      node_id:main.id, variant_properties:main.variantProperties || {}, remote:main.remote};
  }
  nodes.push(item);
  if ("children" in node) for (const child of node.children) await visit(child);
}
await visit(page);
if (nodes.length !== originals.size) errors.push("Original and inspected node trees differ");
// Include unused local tokens/modes, plus remote bindings, action dependencies and aliases.
for (const variable of await figma.variables.getLocalVariablesAsync()) variableIds.add(variable.id);
for (const collection of await figma.variables.getLocalVariableCollectionsAsync()) collectionIds.add(collection.id);
for (const id of collectionIds) {
  const collection = await figma.variables.getVariableCollectionByIdAsync(id);
  if (!collection) errors.push("Unreadable variable collection " + id);
  else for (const variableId of collection.variableIds) variableIds.add(variableId);
}
const variables = [], loaded = new Set();
for (const id of variableIds) {
  if (loaded.has(id)) continue;
  loaded.add(id);
  const v = await figma.variables.getVariableByIdAsync(id);
  if (!v) { errors.push("Unreadable variable " + id); continue; }
  const collection = await figma.variables.getVariableCollectionByIdAsync(v.variableCollectionId);
  if (!collection) { errors.push("Unreadable variable collection " + v.variableCollectionId); continue; }
  for (const mode of collection.modes) {
    if (!(mode.modeId in v.valuesByMode)) { errors.push("Missing variable mode "+id+":"+mode.modeId); continue; }
    const value=v.valuesByMode[mode.modeId]; aliases(value);
    variables.push({key:v.key+":"+mode.modeId,source_id:v.id,name:v.name,kind:v.resolvedType,
      value,collection:collection.name,mode:mode.name,mode_id:mode.modeId});
  }
}
return {version:1,file_key:fileKey,page_id:pageId,read_only:true,complete:errors.length === 0,errors,nodes,variables};
