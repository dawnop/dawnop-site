import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import prettier from 'eslint-config-prettier'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },

  js.configs.recommended,

  // 'essential' is vue's loosest tier: the rules that catch actual errors rather
  // than naming and ordering taste. Same reasoning as the ktlint baseline in the
  // sibling repo — layout stays hand-owned, the linter looks for mistakes.
  ...pluginVue.configs['flat/essential'],

  // Must stay last: switches off every rule that would fight prettier.
  prettier,

  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        // Injected at build time by unplugin-auto-import's ElementPlusResolver:
        // bare ElMessage/ElMessageBox are rewritten into real imports (and their
        // styles pulled in) by the plugin, so they never appear as imports here.
        // Declaring them is what keeps that from reading as 34 undefined vars.
        ElMessage: 'readonly',
        ElMessageBox: 'readonly',
        ElNotification: 'readonly',
        ElLoading: 'readonly',
      },
    },
    rules: {
      // Pre-v9 default. v9 turned on checking of caught-error bindings, which
      // flags 47 `catch (e) {}` where the binding is deliberately ignored. That
      // is a style call (`catch {}` is the modern spelling), not a defect, and
      // rewriting 47 of them buys nothing today.
      'no-unused-vars': ['error', { caughtErrors: 'none' }],

      // Not in js:recommended, enabled deliberately. viz/runtime.js has the one
      // legitimate `new Function` (it revives the component the author compiled
      // and saved) and already carries a disable comment explaining its trust
      // model. Turning the rule on is what gives that comment force — and makes
      // the *next* new Function or eval arrive as a decision instead of a diff.
      'no-new-func': 'error',
      'no-eval': 'error',
    },
  },

  {
    // Build/tooling files run in node, not the browser.
    files: ['vite.config.js', 'eslint.config.js', 'scripts/**/*.js'],
    languageOptions: { globals: { ...globals.node } },
  },

  {
    // viz components are single-purpose demos the author names freely
    // ("coalescing"); the multi-word convention exists to avoid clashing with
    // HTML elements, which these never do — they are mounted by the viz runtime.
    files: ['scripts/viz-seed/**/*.vue', 'src/viz/**/*.vue'],
    rules: { 'vue/multi-word-component-names': 'off' },
  },
]
