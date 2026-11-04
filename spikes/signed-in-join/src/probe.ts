import { once } from 'node:events';
import fs from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { parseArgs } from 'node:util';
import {
  coverScreenshots,
  env,
  errorText,
  fail,
  launch,
  navigatorFacts,
  profilePath,
  RunLog,
  sleep,
  versions,
  type BrowserName,
} from './common.ts';
import { readPage } from './page.ts';

const { values: args } = parseArgs({
  options: {
    browser: { type: 'string', default: 'chrome' },
    headless: { type: 'boolean', default: false },
    profile: { type: 'string' },
    'check-login': { type: 'boolean', default: false },
  },
});
const browser = args.browser as BrowserName;
if (browser !== 'chrome' && browser !== 'chromium') fail('--browser must be chrome or chromium.');
if (args['check-login'] && !args.profile) fail('--check-login needs --profile.');
let userDataDir: string;
if (args.profile) {
  userDataDir = profilePath(args.profile);
  if (!fs.existsSync(userDataDir)) fail(`No saved login at .auth/${args.profile}: sign in by hand first (README.md).`);
  if (!env.BOT_ACCOUNT_NAME || !env.BOT_ACCOUNT_EMAIL) {
    fail('Set BOT_ACCOUNT_NAME and BOT_ACCOUNT_EMAIL in .env first, so that logs and screenshots leave them out.');
  }
} else {
  userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 't0.2-probe-'));
}

const log = new RunLog(`probe-${browser}${args.headless ? '-headless' : ''}${args.profile ? '-profile' : ''}`);
log.line('run.start', {
  browser,
  headless: args.headless,
  profile: args.profile ? `.auth/${args.profile}` : 'none (new and empty)',
  ...versions(),
});
if (args.profile) log.line('profile', { created: fs.statSync(userDataDir).birthtime.toISOString() });

const server = http.createServer((_, res) => res.end('<!doctype html><title>T0.2 probe</title><p>T0.2 probe</p>'));
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const context = await launch(browser, userDataDir, args.headless);
try {
  log.line('browser', { version: context.browser()?.version() });
  const page = context.pages()[0] ?? (await context.newPage());

  await page.goto(`http://127.0.0.1:${(server.address() as AddressInfo).port}/`);
  log.line('navigator', await navigatorFacts(page));
  const media = await page
    .evaluate(async () => {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
      const tracks = stream.getTracks().map((track) => `${track.kind}: ${track.label}`);
      for (const track of stream.getTracks()) track.stop();
      const devices = (await navigator.mediaDevices.enumerateDevices()).map((d) => `${d.kind}: ${d.label}`);
      return { tracks, devices };
    })
    .catch((error) => ({ error: errorText(error) }));
  log.line('media', media);

  if (args['check-login']) {
    await page.goto('https://accounts.google.com/?hl=en', { waitUntil: 'domcontentloaded' });
    await sleep(3_000);
    const landed = new URL(page.url());
    log.line('login', { landed: landed.host + landed.pathname, signedIn: landed.host === 'myaccount.google.com' });
    log.line('capture', { file: await log.capture(page, 'login', await page.evaluate(readPage)) });
  }

  try {
    await page.goto('chrome://version');
    const ids = ['version', 'executable_path', 'profile_path', 'command_line'];
    log.line('chrome-version', await page.evaluate((ids) => Object.fromEntries(ids.map((id) => [id, document.getElementById(id)?.textContent?.trim()])), ids));
  } catch (error) {
    log.line('chrome-version', { error: errorText(error) });
  }
} finally {
  await context.close();
  server.close();
  if (!args.profile) fs.rmSync(userDataDir, { recursive: true, force: true });
  if (args['check-login']) log.line('screenshots', coverScreenshots(log.dir));
  log.line('run.end');
}
