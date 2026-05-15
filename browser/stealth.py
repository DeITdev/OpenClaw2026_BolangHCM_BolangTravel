"""Minimal browser stealth — replaces playwright-stealth.

playwright-stealth 1.0.6 injects broken scripts (utils/log/opts not defined)
that crash before Google Maps can initialize. This module does only the
essential patches needed to pass Google's basic automation detection without
throwing any JS errors.

Patches applied:
  1. navigator.webdriver → undefined  (most important)
  2. navigator.plugins   → fake plugin list (non-empty)
  3. navigator.languages → ['id', 'en-US', 'en']
  4. window.chrome       → minimal chrome runtime object
  5. permissions.query   → fake 'granted' for notification
"""

from __future__ import annotations

from playwright.async_api import Page

_STEALTH_JS = """
(function () {
  // 1. Hide webdriver flag
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });

  // 2. Fake plugin list (headless has none, real Chrome has several)
  const fakePlugin = (name, filename, description) => {
    const plugin = Object.create(Plugin.prototype);
    Object.defineProperty(plugin, 'name',        { value: name });
    Object.defineProperty(plugin, 'filename',    { value: filename });
    Object.defineProperty(plugin, 'description', { value: description });
    Object.defineProperty(plugin, 'length',      { value: 0 });
    return plugin;
  };
  const pluginArray = Object.create(PluginArray.prototype);
  const plugins = [
    fakePlugin('Chrome PDF Plugin',  'internal-pdf-viewer',        'Portable Document Format'),
    fakePlugin('Chrome PDF Viewer',  'mhjfbmdgcfjbbpaeojofohoefgiehjai', ''),
    fakePlugin('Native Client',      'internal-nacl-plugin',       ''),
  ];
  plugins.forEach((p, i) => Object.defineProperty(pluginArray, i, { value: p }));
  Object.defineProperty(pluginArray, 'length', { value: plugins.length });
  Object.defineProperty(navigator, 'plugins', { get: () => pluginArray });

  // 3. Languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['id', 'en-US', 'en'],
  });

  // 4. window.chrome shim
  if (!window.chrome) {
    window.chrome = {
      app: { isInstalled: false },
      runtime: {
        PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
        PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
        RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
        OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
      },
    };
  }

  // 5. Permissions API — report notification as 'default' instead of raising
  if (navigator.permissions && navigator.permissions.query) {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {
      if (params && params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission, onchange: null });
      }
      return origQuery(params);
    };
  }
})();
"""


async def apply_stealth(page: Page) -> None:
    """Inject stealth patches into every new document before any page JS runs."""
    await page.add_init_script(_STEALTH_JS)
