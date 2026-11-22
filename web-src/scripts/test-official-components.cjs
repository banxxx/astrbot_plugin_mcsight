/* Offline component tests. A custom Vue renderer and synthetic API replace the browser,
 * framework bridge, credentials and network; this is not a screenshot/layout test. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createRequire } = require('node:module');

const frontend = path.resolve(__dirname, '..');
const localRequire = createRequire(path.join(frontend, 'package.json'));
const vue = localRequire('vue');
const compiler = localRequire('@vue/compiler-sfc');
const esbuild = localRequire('esbuild');
const { h, defineComponent, createRenderer, reactive, ref, nextTick } = vue;

function node(type, content = '') {
  return { type, content, props: {}, children: [], parent: null, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    removeEventListener(name) { delete this.listeners[name]; },
    getAttribute(name) { return this.props[name]; },
  };
}
const renderer = createRenderer({
  createElement: type => node(type), createText: text => node('#text', text),
  createComment: text => node('#comment', text),
  insert(child, parent, anchor = null) {
    if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1);
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    parent.children.splice(index < 0 ? parent.children.length : index, 0, child); child.parent = parent;
  },
  remove(child) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); child.parent = null; },
  setElementText(target, text) { target.content = text; target.children = []; },
  setText(target, text) { target.content = text; },
  patchProp(target, key, _previous, value) { target.props[key] = value; },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
});
function all(root, predicate) {
  return (predicate(root) ? [root] : []).concat(root.children.flatMap(child => all(child, predicate)));
}
function content(root) { return root.content + root.children.map(content).join(''); }
function one(root, predicate) {
  const found = all(root, predicate); assert.equal(found.length, 1, `Expected one node, found ${found.length}`); return found[0];
}
async function settle() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick(); } }
async function click(root, label) {
  const target = one(root, n => n.type === 'button' && content(n) === label);
  assert(!target.props.disabled, label + ' is disabled'); await target.props.onClick(); await settle();
}
function mockComponent(name) {
  if (name === 'UiSelect') return defineComponent({ props: ['modelValue', 'options', 'disabled', 'ariaLabel'], emits: ['update:modelValue'],
    setup(props, { emit }) { return () => h('select', { 'aria-label': props.ariaLabel, disabled: props.disabled,
      onChange: value => emit('update:modelValue', value) }, props.options.map(o => h('option', { value: o.value }, o.label))); } });
  if (name === 'UiInput' || name === 'UiSecretInput') return defineComponent({ props: ['modelValue', 'disabled', 'placeholder'], emits: ['update:modelValue'],
    setup(props, { emit, attrs }) { return () => h('input', { ...attrs, disabled: props.disabled, placeholder: props.placeholder,
      value: props.modelValue, onInput: value => emit('update:modelValue', value) }); } });
  if (name === 'UiButton') return defineComponent({ props: ['disabled', 'kind', 'loading'],
    setup(props, { slots, attrs }) { return () => h('button', { ...attrs, disabled: props.disabled || props.loading }, slots.default?.()); } });
  if (name === 'UiSwitch') return defineComponent({ props: ['on', 'disabled'], emits: ['toggle'],
    setup(props, { emit, attrs }) { return () => h('button', { ...attrs, disabled: props.disabled, onClick: () => emit('toggle') }, String(props.on)); } });
  return defineComponent({ setup(_, { slots }) { return () => h('aside', {}, slots.default?.()); } });
}
globalThis.__officialTestWidgets = Object.fromEntries(['UiSelect', 'UiInput', 'UiSecretInput', 'UiButton', 'UiSwitch', 'UiNotice', 'UiBadge']
  .map(name => [name, mockComponent(name)]));

async function compile(filename) {
  const result = await esbuild.build({
    entryPoints: [path.join(frontend, 'src', filename)], bundle: true, write: false, platform: 'node', format: 'cjs',
    external: ['vue'], logLevel: 'silent', plugins: [{ name: 'offline-vue', setup(build) {
      build.onResolve({ filter: /store$/ }, () => ({ path: 'synthetic-store', namespace: 'synthetic' }));
      build.onLoad({ filter: /.*/, namespace: 'synthetic' }, () => ({ contents:
        'export const S=globalThis.__officialTestFixture.S; export const api=globalThis.__officialTestFixture.api; export const configSaving=globalThis.__officialTestFixture.configSaving; export const officialBusy=globalThis.__officialTestFixture.officialBusy; export const officialDraftPending=globalThis.__officialTestFixture.officialDraftPending; export const toast=globalThis.__officialTestFixture.toast;' }));
      build.onResolve({ filter: /\/Ui\w+\.vue$/ }, args => ({ path: path.basename(args.path, '.vue'), namespace: 'widget' }));
      build.onLoad({ filter: /.*/, namespace: 'widget' }, args => ({ contents: `export default globalThis.__officialTestWidgets[${JSON.stringify(args.path)}];` }));
      build.onLoad({ filter: /\.vue$/ }, async args => {
        assert(args.path.startsWith(path.join(frontend, 'src') + path.sep));
        const source = await fs.readFile(args.path, 'utf8');
        const { descriptor } = compiler.parse(source, { filename: args.path });
        const script = compiler.compileScript(descriptor, { id: 'offline-component', inlineTemplate: true });
        return { contents: script.content, loader: 'ts', resolveDir: path.dirname(args.path) };
      });
    } }],
  });
  const output = { exports: {} };
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(localRequire, output, output.exports);
  return output.exports.default;
}

const ENV = '11111111-1111-4111-8111-111111111111';
const CONFIG = '22222222-2222-4222-8222-222222222222';
const CANDIDATE = '33333333-3333-4333-8333-333333333333';
const OPERATION = '44444444-4444-4444-8444-444444444444';
const clone = value => JSON.parse(JSON.stringify(value));
function fixture() {
  const selection = { platform: 'qq_official', instance_id: 'official-a', environment_id: ENV, application_ref: 'SYNTHETIC_APP', config_revision: 1 };
  const activation = { selection, saved_selection: selection, config_revision: 1,
    state: 'configuring', message: '合成测试', registered: true, online: 'unknown', adapter_status: 'running',
    pending_restart: false, restart_required: false, choices: [{ platform: 'qq_official', instance_id: 'official-a', adapter: 'qq_official', adapter_status: 'running' }],
    environments: [{ environment_id: ENV, platform: 'qq_official', application_ref: 'SYNTHETIC_APP' }] };
  let state = { schema_version: 1, environment_id: ENV, revision: 8, authorization_history_count: 0,
    message_delivery: { ready: true, receipts_count: 0, capacity: 4096, message: '合成发送状态正常' },
    active: true, binding_ready: false, has_mod_token: false, environment_administrators: [],
    candidates: [{ candidate_id: CANDIDATE, group_openid: 'SYNTHETIC_GROUP', member_openid: 'SYNTHETIC_USER',
      nickname: '合成玩家', note: '登记备注', created_at: 1, expires_at: 9999999999 }],
    groups: [{ config_id: CONFIG, group_openid: 'SYNTHETIC_GROUP', revision: 2, alias: '合成群', display_group: '', enabled: true,
      servers: [], administrators: [], blacklist: [], nickname_ignore: [],
      policy: { enable_group_owner: true, enable_group_admin: true, bind_require_nickname: true, bind_nickname_rule: 'paren',
        bind_nickname_template: '', show_server_version: false, show_server_latency: true, show_last_online: false } }] };
  const calls = [], messages = [];
  const operations = { state: 'COMMITTED', fail: false, waitAction: null, waitDetail: null };
  function detail() { return { operation_id: OPERATION, kind: 'bind', state: operations.state, result_code: 'SYNC_PENDING',
    payload_hash: 'a'.repeat(64), policy_revision: '2', pending: true, token_state: operations.state === 'COMMITTED' ? 'CONSUMED' : 'PENDING',
    effects: operations.state === 'COMMITTED' ? [{ game_id: 'Player_A', game_key: 'player_a', state: 'active', revision: '1' }] : [],
    delivery: operations.state === 'COMMITTED' ? [{ server_id: '55555555-5555-4555-8555-555555555555', name: 'Synthetic server', mirror: 'APPLIED', whitelist: 'PENDING', online: 'APPLIED' }] : [] }; }
  return { calls, messages, operations, configSaving: ref(false), officialBusy: ref(false), officialDraftPending: ref(false), S: reactive({ activation: clone(activation) }), toast: value => messages.push(value),
    api: {
      activation: async () => clone(activation), officialState: async () => clone(state),
      officialOperations: async (scope, filter, cursor) => {
        assert.equal(scope.environment_id, ENV); assert.equal(scope.config_id, CONFIG); assert.equal(scope.expected_revision, state.groups[0].revision);
        calls.push({ endpoint: 'operations', scope: clone(scope), filter, cursor });
        return { operations: [{ operation_id: OPERATION, kind: 'bind', state: operations.state, result_code: 'SYNC_PENDING',
          policy_revision: '2', created_at: '2026-11-19T00:00:00.000000Z', updated_at: '2026-11-19T00:00:00.000000Z' }], next_cursor: null, total: 1, truncated: false };
      },
      officialOperation: async (scope, operation_id) => {
        calls.push({ endpoint: 'operation', scope: clone(scope), operation_id });
        assert.equal(scope.config_id, CONFIG); assert(operation_id === OPERATION || operation_id === OPERATION.slice(0, 8));
        if (operations.waitDetail) await operations.waitDetail;
        return clone(detail());
      },
      officialOperationAction: async (scope, operation, action) => {
        calls.push({ endpoint: 'operation_action', scope: clone(scope), operation: clone(operation), action });
        if (operations.waitAction) await operations.waitAction;
        if (operations.fail) throw new Error('合成结果待确认，请查询原操作');
        if (action === 'cancel') operations.state = 'ABORTED';
        return { code: 'OK', operation: clone(detail()), recovery: null };
      },
      officialUpdate: async body => {
        calls.push(clone(body)); assert.equal(body.environment_id, ENV);
        if (body.action === 'authorize' || body.action === 'revoke') {
          assert.equal(body.expected_revision, body.role === 'environment_admin' ? state.revision : state.groups[0].revision);
          if (body.role === 'environment_admin') state.environment_administrators = body.action === 'authorize' ? ['SYNTHETIC_USER'] : [];
          state.authorization_history_count++;
        }
        if (body.action === 'save') {
          assert.equal(body.expected_revision, state.groups[0].revision);
          state.groups[0] = { ...state.groups[0], ...clone(body.changes), revision: state.groups[0].revision + 1 };
        }
        state.revision++; return clone(state);
      },
    },
  };
}

(async () => {
  const current = fixture(); globalThis.__officialTestFixture = current;
  const component = await compile('pages/OfficialPage.vue');
  const root = node('root'); const app = renderer.createApp(component); app.mount(root); await settle();
  assert(content(root).includes('群配置'));
  assert(content(root).includes('合成玩家'));
  const candidate = one(root, n => n.type === 'select' && n.props['aria-label'] === '身份候选');
  candidate.props.onChange(CANDIDATE); await settle();
  assert(one(root, n => n.type === 'button' && content(n) === '保存该身份授权').props.disabled);
  const check = one(root, n => n.type === 'input' && n.props.type === 'checkbox' && content(n.parent).includes('我已对照实际发言核对登记回执和身份'));
  check.checked = true; check.listeners.change(); await settle();
  one(root, n => n.type === 'select' && n.props['aria-label'] === '授权角色').props.onChange('environment_admin'); await settle();
  await click(root, '保存该身份授权');
  assert.equal(current.calls.at(-1).role, 'environment_admin');
  assert.equal(current.calls.at(-1).expected_revision, 8);
  await click(root, '撤销 环境超管');
  assert.equal(current.calls.at(-1).action, 'revoke');
  assert.equal(current.calls.at(-1).role, 'environment_admin');
  await click(root, '添加服务器');
  assert(one(root, n => n.type === 'select' && n.props['aria-label'] === '官方应用环境').props.disabled);
  assert(one(root, n => n.type === 'button' && content(n) === '保存该身份授权').props.disabled);
  assert(content(root).includes('未保存的更改'));
  assert.equal(current.officialDraftPending.value, true);
  await click(root, '放弃草稿并刷新');
  assert.equal(current.officialDraftPending.value, false);
  await click(root, '查询操作列表');
  assert.equal(current.calls.at(-1).endpoint, 'operations');
  await click(root, '查看操作');
  assert(content(root).includes(OPERATION));
  assert(content(root).includes('镜像 APPLIED 不等于全部完成'));
  assert(one(root, n => n.type === 'button' && content(n) === '重查终态并补偿').props.disabled);
  const confirmation = () => one(root, n => n.type === 'input' && n.props.type === 'checkbox' && content(n.parent).includes('我已核对上述操作编号'));
  const confirm = async () => { const checkbox = confirmation(); checkbox.checked = true; checkbox.listeners.change(); await settle(); };
  await confirm();
  assert(one(root, n => n.type === 'button' && content(n) === '中止未提交操作').props.disabled);
  let releaseAction;
  current.operations.waitAction = new Promise(resolve => { releaseAction = resolve; });
  const retry = one(root, n => n.type === 'button' && content(n) === '重查终态并补偿').props.onClick();
  await settle();
  assert.equal(current.officialBusy.value, true);
  assert(one(root, n => n.type === 'select' && n.props['aria-label'] === '官方应用环境').props.disabled);
  releaseAction(); await retry; await settle(); current.operations.waitAction = null;
  assert.equal(current.officialBusy.value, false);
  assert(content(root).includes('仍有分项待确认'));
  assert(one(root, n => n.type === 'button' && content(n) === '重查终态并补偿').props.disabled);
  await confirm(); current.operations.fail = true;
  await click(root, '重查终态并补偿');
  assert(confirmation().props.disabled);
  assert(content(root).includes('请重新查询原操作'));
  assert.equal(current.calls.filter(call => call.endpoint === 'operation_action').length, 2);
  current.operations.fail = false; current.operations.state = 'PREPARED';
  await click(root, '查询原操作');
  await confirm();
  assert(one(root, n => n.type === 'button' && content(n) === '重查终态并补偿').props.disabled);
  assert(!one(root, n => n.type === 'button' && content(n) === '中止未提交操作').props.disabled);
  await click(root, '中止未提交操作');
  assert(content(root).includes('ABORTED'));
  assert(content(root).includes('验证码作废及其它分项仍以当前状态为准'));
  app.unmount();

  const isolated = fixture(); globalThis.__officialTestFixture = isolated;
  const operationsComponent = await compile('components/OfficialOperations.vue');
  const busyEvents = [];
  const properties = reactive({ environment: ENV, configId: CONFIG, revision: 2, enabled: true, blocked: false, onBusy: value => busyEvents.push(value) });
  const isolatedRoot = node('root');
  const isolatedApp = renderer.createApp(defineComponent({ setup: () => () => h(operationsComponent, properties) }));
  isolatedApp.mount(isolatedRoot); await settle();
  const lookup = one(isolatedRoot, n => n.type === 'input' && n.props['aria-label'] === '原操作编号');
  lookup.props.onInput(OPERATION); await settle();
  let releaseDetail;
  isolated.operations.waitDetail = new Promise(resolve => { releaseDetail = resolve; });
  const pending = one(isolatedRoot, n => n.type === 'button' && content(n) === '查询原操作').props.onClick();
  await settle();
  properties.configId = '66666666-6666-4666-8666-666666666666'; properties.revision = 3; await settle();
  releaseDetail(); await pending; await settle();
  assert(!content(isolatedRoot).includes(OPERATION));
  assert(!content(isolatedRoot).includes('Player_A'));
  assert.deepEqual(busyEvents, [true, false]);
  isolatedApp.unmount();
  delete globalThis.__officialTestFixture; delete globalThis.__officialTestWidgets;
  console.log('PASS: offline Vue mount, candidate confirmation, environment revisions, dirty protection, official operation scope, explicit recovery confirmation, unknown-result lock, stale response rejection and parent busy protection.');
})().catch(error => { console.error(error); process.exitCode = 1; });
