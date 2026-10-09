const { defineConfig } = require('vitest/config');

module.exports = defineConfig({
  cacheDir: '.vitest',
  server: { host: '127.0.0.1' },
  test: {
    include: ['tests/**/*.test.ts', '_oracle_hidden/**/*.test.ts'],
    environment: 'node',
  },
});
