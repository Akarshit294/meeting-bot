import fs from 'node:fs';
import { parseArgs } from 'node:util';
import type { Locator } from 'playwright';
import {
  coverScreenshots,
  env,
  errorText,
  fail,
  launch,
  navigatorFacts,
  Owner,
  profilePath,
  RunLog,
  sleep,
  versions,
} from './common.ts';
import { Watcher } from './page.ts';

const { values: args } = parseArgs({
  options: {
    mode: { type: 'string', default: '' },
    try: { type: 'string', default: '' },
  },
});
const mode = args.mode;
if (mode !== 'hand' && mode !== 'script') fail('--mode must be hand (step 13.1) or script (step 13.2).');
if (args.try !== '1' && args.try !== '2') fail('--try must be 1 or 2: at most 2 tries of each (step 13.4).');
if (/pw:|\*/.test(process.env.DEBUG ?? '') || process.env.PWDEBUG) {
  fail('Unset DEBUG and PWDEBUG first: Playwright debug logs can record typed text.');
}
if (!env.BOT_ACCOUNT_NAME || !env.BOT_ACCOUNT_EMAIL) {
  fail('Set BOT_ACCOUNT_NAME and BOT_ACCOUNT_EMAIL in .env first, so that logs and screenshots leave them out.');
}
if (mode === 'script' && !env.BOT_ACCOUNT_PASSWORD) fail('Set BOT_ACCOUNT_PASSWORD in .env first.');
if (mode === 'hand' && !process.stdin.isTTY) fail('--mode hand needs the owner at this terminal.');
const profile = `T0.2-signin-${mode}-${args.try}`;
const userDataDir = profilePath(profile);
if (fs.existsSync(userDataDir)) fail(`.auth/${profile} exists already: each try needs a new, empty profile folder.`);
fs.mkdirSync(userDataDir, { recursive: true });

const log = new RunLog(`signin-${mode}-${args.try}`);
const owner = new Owner(log);
owner.stopOnCtrlC();
log.line('run.start', {
  step: mode === 'hand' ? '13.1, by hand' : '13.2, by script',
  try: args.try,
  profile: `.auth/${profile}`,
  browser: 'chrome',
  headless: false,
  ...versions(),
});
const context = await launch('chrome', userDataDir, false);
log.line('browser', { version: context.browser()?.version() });
const page = context.pages()[0] ?? (await context.newPage());
const watcher = new Watcher(page, log, (flags) => flags.url);
let result = 'unfinished';
try {
  log.line('goto', { url: 'https://accounts.google.com/?hl=en' });
  await page.goto('https://accounts.google.com/?hl=en', { waitUntil: 'domcontentloaded' });
  log.line('navigator', await navigatorFacts(page));
  watcher.start();
  result = mode === 'hand' ? await byHand() : await byScript();
  await sleep(3_000);
  await page.goto('https://accounts.google.com/?hl=en', { waitUntil: 'domcontentloaded' });
  await sleep(3_000);
  const landed = new URL(page.url());
  log.line('login', { landed: landed.host + landed.pathname, signedIn: landed.host === 'myaccount.google.com' });
} catch (error) {
  result = `error: ${errorText(error)}`;
} finally {
  watcher.stop();
  await context.close().catch(() => {});
  log.line('screenshots', coverScreenshots(log.dir));
  log.line('run.end', { result, pages: watcher.history.map((h) => h.state).join(' > ') });
  owner.close();
}

async function byHand(): Promise<string> {
  const { pressed } = owner.enter(
    "Sign in to the bot's account in the browser window, by hand. You may answer any check Google shows. Press Enter here when you are signed in, or when Google will not let you.",
  );
  await pressed;
  return `the owner says: ${await owner.ask('Did the sign-in work? (y/n, and what Google showed)')}`;
}

async function byScript(): Promise<string> {
  const email = page.getByRole('textbox', { name: /email or phone/i }).or(page.locator('input[type="email"]'));
  if (!(await visible(email, 20_000))) return `stopped: no email field (${watcher.state})`;
  await email.fill(env.BOT_ACCOUNT_EMAIL!);
  log.line('fill.email');
  await clickNext();

  const password = page.locator('input[type="password"]');
  if (!(await visible(password, 20_000))) return `stopped after the email: no password field (${watcher.state})`;
  await sleep(1_000);
  await password.fill(env.BOT_ACCOUNT_PASSWORD!);
  log.line('fill.password');
  await clickNext();
  await page.waitForURL((url) => !url.pathname.includes('/challenge/pwd'), { timeout: 30_000 }).catch(() => {});
  await sleep(3_000);
  return `after the password: ${watcher.state}`;
}

async function clickNext() {
  await page.getByRole('button', { name: /^next$/i }).first().click({ timeout: 10_000 });
  log.line('click.next');
}

function visible(locator: Locator, timeout: number): Promise<boolean> {
  return locator.first().waitFor({ state: 'visible', timeout }).then(
    () => true,
    () => false,
  );
}
