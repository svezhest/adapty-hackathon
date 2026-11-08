/* Standalone runtime for the Claude Design hand-off files (*.dc.html).
 *
 * The design files stay exactly as exported: <x-dc> holds <helmet> (head content) and the markup;
 * <script type="text/x-dc"> defines `class Component extends DCLogic` whose renderVals() returns the
 * values the markup uses. Supported: {{ path }} in text and attributes, <sc-for list as>, <sc-if value>,
 * on* attributes bound to functions, setState / state / props / componentDidMount.
 * Rendering morphs the live DOM in place, so focus and caret survive a re-render.
 */
(function () {
  'use strict';

  var EVENTS = { onClick: 'click', onSubmit: 'submit', onKeyDown: 'keydown', onMouseEnter: 'mouseenter', onMouseLeave: 'mouseleave', onInput: 'input', onBlur: 'blur', onFocus: 'focus' };
  var BOOL_ATTRS = { checked: 1, disabled: 1, selected: 1, hidden: 1, readonly: 1, required: 1, open: 1, autofocus: 1 };
  var ONE = /^\s*\{\{\s*([^}]+?)\s*\}\}\s*$/;
  var ANY = /\{\{\s*([^}]+?)\s*\}\}/g;

  function lookup(scope, path) {
    path = path.trim();
    if (path === 'true') return true;
    if (path === 'false') return false;
    if (path === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(path)) return Number(path);
    var parts = path.split('.');
    var v = scope;
    for (var i = 0; i < parts.length; i++) {
      if (v == null) return undefined;
      v = v[parts[i]];
    }
    return v;
  }

  function text(v) { return v == null || v === false ? '' : String(v); }

  function changeEvent(el) {
    var tag = el.tagName;
    var type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'SELECT' || type === 'checkbox' || type === 'radio' || type === 'file') return 'change';
    return 'input';
  }

  // --- template → fresh DOM (no listeners yet; handlers kept in el.__h) ---------------------------

  function build(node, scope, out) {
    if (node.nodeType === 3) {
      out.appendChild(document.createTextNode(node.nodeValue.replace(ANY, function (_, p) { return text(lookup(scope, p)); })));
      return;
    }
    if (node.nodeType !== 1) return;
    var tag = node.tagName.toLowerCase();
    var sc = tag === 'template' ? node.getAttribute('data-sc') : null;
    if (sc === 'for') tag = 'sc-for';
    if (sc === 'if') tag = 'sc-if';
    var kids = function () { var src = node.content || node; return Array.prototype.slice.call(src.childNodes); };
    if (tag === 'sc-for') {
      var list = lookup(scope, (node.getAttribute('list') || '').replace(ONE, '$1')) || [];
      var as = node.getAttribute('as') || 'item';
      for (var i = 0; i < list.length; i++) {
        var inner = Object.create(scope);
        inner[as] = list[i];
        inner[as + 'Index'] = i;
        kids().forEach(function (c) { build(c, inner, out); });
      }
      return;
    }
    if (tag === 'sc-if') {
      var m = (node.getAttribute('value') || '').match(ONE);
      if (m && lookup(scope, m[1])) kids().forEach(function (k) { build(k, scope, out); });
      return;
    }
    var el = document.createElementNS(node.namespaceURI, node.localName);
    el.__h = {};
    for (var a = 0; a < node.attributes.length; a++) {
      var attr = node.attributes[a];
      var name = attr.name, raw = attr.value;
      // sc-d="{{ x }}": an SVG attribute that waits for data; the browser would reject the raw {{ }} while parsing.
      if (name.indexOf('sc-') === 0) name = name.slice(3);
      var one = raw.match(ONE);
      var evName = null;
      for (var key in EVENTS) if (key.toLowerCase() === name) evName = EVENTS[key];
      if (name === 'onchange') evName = 'change*';
      if (evName && one) {
        var fn = lookup(scope, one[1]);
        if (typeof fn === 'function') el.__h[evName] = fn;
        continue;
      }
      if (one && BOOL_ATTRS[name]) {
        if (lookup(scope, one[1])) el.setAttribute(name, '');
        continue;
      }
      if (one && name === 'value') {
        el.__value = text(lookup(scope, one[1]));
        el.setAttribute('value', el.__value);
        continue;
      }
      el.setAttribute(name, one ? text(lookup(scope, one[1])) : raw.replace(ANY, function (_, p) { return text(lookup(scope, p)); }));
    }
    if (el.__h['change*']) { el.__h[changeEvent(el)] = el.__h['change*']; delete el.__h['change*']; }
    var into = tag === 'template' ? el.content : el;
    kids().forEach(function (ch) { build(ch, scope, into); });
    out.appendChild(el);
  }

  // --- morph the live DOM to match the fresh one --------------------------------------------------

  function bind(el, h) {
    el.__h = h || {};
    el.__bound = el.__bound || {};
    for (var type in el.__h) {
      if (el.__bound[type]) continue;
      el.__bound[type] = true;
      (function (t) {
        el.addEventListener(t, function (e) {
          var f = el.__h[t];
          if (f) f(e);
        });
      })(type);
    }
  }

  function syncAttrs(old, neu) {
    for (var i = old.attributes.length - 1; i >= 0; i--) {
      var n = old.attributes[i].name;
      if (!neu.hasAttribute(n)) old.removeAttribute(n);
    }
    for (var j = 0; j < neu.attributes.length; j++) {
      var a = neu.attributes[j];
      if (old.getAttribute(a.name) !== a.value) old.setAttribute(a.name, a.value);
    }
    if ('checked' in old && (old.type === 'checkbox' || old.type === 'radio')) old.checked = neu.hasAttribute('checked');
    if (neu.__value !== undefined && old.value !== neu.__value) old.value = neu.__value;
    if (old.tagName === 'SELECT' || old.tagName === 'OPTION') {
      if (old.tagName === 'OPTION') old.selected = neu.hasAttribute('selected');
    }
  }

  function same(a, b) {
    return a.nodeType === b.nodeType && a.nodeName === b.nodeName;
  }

  function morphChildren(old, neu) {
    var o = old.firstChild, n = neu.firstChild;
    while (n) {
      var nextN = n.nextSibling;
      if (!o) {
        old.appendChild(n);
        bindTree(n);
      } else if (same(o, n)) {
        morph(o, n);
        o = o.nextSibling;
      } else {
        var nextO = o.nextSibling;
        old.replaceChild(n, o);
        bindTree(n);
        o = nextO;
      }
      n = nextN;
    }
    while (o) {
      var rm = o;
      o = o.nextSibling;
      old.removeChild(rm);
    }
  }

  function morph(old, neu) {
    if (old.nodeType === 3) {
      if (old.nodeValue !== neu.nodeValue) old.nodeValue = neu.nodeValue;
      return;
    }
    if (old.nodeType !== 1) return;
    syncAttrs(old, neu);
    bind(old, neu.__h);
    if (old.tagName === 'TEXTAREA') {
      var v = neu.__value !== undefined ? neu.__value : neu.textContent;
      if (old.value !== v) old.value = v;
      return;
    }
    if (old.tagName === 'SELECT') {
      morphChildren(old, neu);
      if (neu.__value !== undefined) old.value = neu.__value;
      return;
    }
    morphChildren(old, neu);
  }

  function bindTree(el) {
    if (el.nodeType !== 1) return;
    bind(el, el.__h);
    if (el.tagName === 'TEXTAREA' && el.__value !== undefined) el.value = el.__value;
    if (el.tagName === 'SELECT' && el.__value !== undefined) el.value = el.__value;
    for (var c = el.firstChild; c; c = c.nextSibling) bindTree(c);
  }

  // --- component base ---------------------------------------------------------------------------

  function DCLogic(props) {
    this.props = props || {};
    this.state = {};
  }
  DCLogic.prototype.setState = function (patch) {
    var s = Object.assign({}, this.state || {});
    if (typeof patch === 'function') patch = patch(s);
    Object.assign(s, patch || {});
    this.state = s;
    if (this.__schedule) this.__schedule();
  };
  window.DCLogic = DCLogic;

  function query() {
    var out = {};
    new URLSearchParams(location.search).forEach(function (v, k) { out[k] = v; });
    return out;
  }

  // Hide the raw template until the first render.
  var hide = document.createElement('style');
  hide.textContent = 'x-dc{display:none!important}';
  document.head.appendChild(hide);

  function start() {
    // The HTML parser moves unknown elements out of <tbody>/<select>, so re-parse the raw source with
    // <sc-for>/<sc-if> turned into <template data-sc> (allowed anywhere; children stay in place).
    fetch(location.href.split('#')[0], { cache: 'no-store' }).then(function (r) { return r.text(); }).then(function (src) {
      src = src.replace(/<sc-(for|if)\b/g, '<template data-sc="$1"').replace(/<\/sc-(for|if)>/g, '</template>');
      var doc = new DOMParser().parseFromString(src, 'text/html');
      mount(doc.querySelector('x-dc'), doc.querySelector('script[type="text/x-dc"]'));
    });
  }

  function mount(source, script) {
    var host = document.querySelector('x-dc');
    if (!host || !source || !script) return;
    var helmet = source.querySelector('helmet');
    if (helmet) {
      Array.prototype.slice.call(helmet.childNodes).forEach(function (n) { document.head.appendChild(document.importNode(n, true)); });
      helmet.remove();
    }
    var tpl = document.createElement('template');
    Array.prototype.slice.call(source.childNodes).forEach(function (n) { tpl.content.appendChild(document.importNode(n, true)); });
    var root = document.createElement('div');
    root.id = 'dc-root';
    host.replaceWith(root);

    var Component = new Function('DCLogic', script.textContent + '\n;return Component;')(DCLogic);
    // data-props is the canvas's prop schema ({name: {editor, default}}): a page gets the defaults.
    var schema = {};
    try { schema = JSON.parse(script.getAttribute('data-props') || '{}'); } catch (e) { schema = {}; }
    var props = {};
    Object.keys(schema).forEach(function (k) {
      if (k.charAt(0) !== '$' && schema[k] && typeof schema[k] === 'object' && 'default' in schema[k]) props[k] = schema[k]['default'];
    });
    Object.assign(props, query());
    var comp = new Component(props);
    comp.props = props;
    comp.state = comp.state || {};

    var queued = false;
    function render() {
      queued = false;
      var vals = comp.renderVals() || {};
      var frag = document.createElement('div');
      for (var n = tpl.content.firstChild; n; n = n.nextSibling) build(n, vals, frag);
      morphChildren(root, frag);
    }
    comp.__schedule = function () {
      if (queued) return;
      queued = true;
      Promise.resolve().then(render);
    };
    render();
    // On a phone the nav scrolls sideways: start with the current screen in view.
    var nav = root.querySelector('.top nav'), here = nav && nav.querySelector('.nl.on');
    if (here && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = here.offsetLeft - nav.offsetLeft - (nav.clientWidth - here.offsetWidth) / 2;
    if (typeof comp.componentDidMount === 'function') comp.componentDidMount();
    window.__dc = comp;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
