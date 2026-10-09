const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync('/Users/ankurkothari/Documents/workspace/agent-console/dashboard_app.js','utf8');
const functions=source.slice(source.indexOf('function captureControls()'),source.indexOf('function disclosure('));
let input={dataset:{focusKey:'approval:fixture'},selectionStart:3,selectionEnd:3,selectionDirection:'none'};
const document={activeElement:input,querySelectorAll:()=>[input]};
const scope={document,focusVersion:1};vm.createContext(scope);vm.runInContext(functions,scope);
const captured=scope.captureControls();
// The user types during the fetch without changing which field has focus.
input.selectionStart=input.selectionEnd=6;
input={dataset:{focusKey:'approval:fixture'},selectionStart:6,selectionEnd:6,
 focus(){document.activeElement=this;},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}};
document.activeElement={tagName:'BODY'};
scope.restoreFocus(captured);
const result={caret_after_typing:6,caret_after_poll_render:input.selectionStart};
console.log(JSON.stringify(result));fs.writeFileSync('/private/tmp/dashboard-audit-20260920/focus-results.json',JSON.stringify(result));
