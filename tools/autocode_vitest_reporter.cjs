// Vitest 4 reporter. stdout is never proof; publish only a completed run's sidecar.
// Public lifecycle: https://vitest.dev/api/advanced/reporters
const fs = require('node:fs');
const path = require('node:path');

module.exports = class AutoCodeVitestReporter {
  onInit(ctx) {
    this.ctx = ctx;
    this.started = false;
    this.finished = new Set();
  }
  onTestRunStart(specifications) {
    this.started = true;
    this.expected = specifications.length;
  }
  onTestCaseResult(test) {
    this.finished.add(test.id);
  }
  onTestRunEnd(modules, errors, reason) {
    if (!/^4\./.test(this.ctx.version) || typeof this.ctx.config.outputFile !== 'string') {
      throw Error('AutoCode named proof requires Vitest 4 and a runner-owned output file');
    }
    let complete = this.started && modules.length === this.expected && reason !== 'interrupted';
    const files = modules.map(module => {
      const suites = [module, ...module.children.allSuites()];
      const collectionError = suites.some(suite => suite.errors().length > 0);
      const tests = [...module.children.allTests()].map(test => {
        const state = test.result().state;
        if (state === 'pending' || (state !== 'skipped' && !this.finished.has(test.id))) complete = false;
        // Vitest's standard JSON omits hook failures. Its v4 task result retains
        // these states, so a failed beforeEach/afterEach is not an application regression.
        const task = test.task;
        if (!task || (state !== 'skipped' && !task.result)) complete = false;
        const hooks = Object.values(task?.result?.hooks || {});
        const hookError = hooks.some(value => value !== 'pass');
        return {name: test.fullName, state, collection_error: collectionError || hookError};
      });
      return {file: module.moduleId, collection_error: collectionError, tests};
    });
    const report = {protocol: 'autocode-vitest-tests', version: 1,
      vitest_version: this.ctx.version, complete, reason,
      unhandled_errors: errors.length, total: files.reduce((sum, file) => sum + file.tests.length, 0), files};
    const output = this.ctx.config.outputFile;
    fs.mkdirSync(path.dirname(output), {recursive: true});
    fs.writeFileSync(output + '.tmp', JSON.stringify(report) + '\n');
    fs.renameSync(output + '.tmp', output);
  }
};
