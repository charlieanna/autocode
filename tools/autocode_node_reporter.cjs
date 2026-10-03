// Custom TestsStream reporter: https://nodejs.org/api/test.html#custom-reporters
// Keep stdout out of the proof channel. A footer is written only after the stream ends.
const path = require('node:path');
module.exports = async function* (source) {
  yield JSON.stringify({protocol: 'autocode-node-tests', version: 1}) + '\n';
  for await (const {type, data} of source) {
    let row;
    if (['test:start', 'test:pass', 'test:fail'].includes(type)) {
      const file = typeof data.file === 'string' ? path.relative(process.cwd(), data.file).split(path.sep).join('/') : null;
      row = {type: type.slice(5), file, name: data.name, nesting: data.nesting,
        line: data.line, column: data.column};
      if (type !== 'test:start') {
        Object.assign(row, {test_type: data.details?.type, skip: !!data.skip, todo: !!data.todo,
          failure_type: data.details?.error?.failureType,
          file_wrapper: data.line === 1 && data.column === 1 && typeof data.name === 'string'
            && path.resolve(data.name) === path.resolve(data.file || '')});
      }
    } else if (type === 'test:summary' && !data.file) {
      row = {type: 'summary', counts: data.counts};
    }
    if (row) yield JSON.stringify(row) + '\n';
  }
  yield JSON.stringify({type: 'end'}) + '\n';
};
