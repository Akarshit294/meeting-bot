import fs from 'node:fs';
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
  Owner,
  profilePath,
  redactAlso,
  RunLog,
  sleep,
  versions,
  within,
  type BrowserName,
} from './common.ts';
import { endStates, meetState, Watcher } from './page.ts';

const runs: Record<string, string> = {
  ask: 'Asks to join and, once waiting, cancels the request. The owner does nothing (steps 2 and 3).',
  'let-in': 'The owner lets the bot in. The bot leaves 20 s later.',
  refused: 'The owner refuses the bot.',
  removed: 'The owner lets the bot in, then removes it.',
  'call-ended': 'The owner lets the bot in, then ends the call for everyone, or everyone leaves.',
  'bot-leaves': 'The owner lets the bot in. The bot leaves when the owner presses Enter.',
  'bot-withdraws': 'The owner does nothing. The bot cancels its request while it waits.',
  'no-answer': 'The owner does nothing until Meet gives up.',
};
const ownerRuns = ['let-in', 'refused', 'removed', 'call-ended', 'bot-leaves', 'bot-withdraws'];

const { values: args } = parseArgs({
  options: {
    url: { type: 'string' },
    run: { type: 'string', default: '' },
    browser: { type: 'string', default: 'chrome' },
    headless: { type: 'boolean', default: false },
    profile: { type: 'string' },
    guest: { type: 'boolean', default: false },
    name: { type: 'string', default: 'open-meeting-bot (test)' },
    wait: { type: 'string', default: '10' },
    network: { type: 'string' },
  },
});
const run = args.run;
if (!runs[run]) fail(`--run must be one of: ${Object.keys(runs).join(', ')}.`);
const browser = args.browser as BrowserName;
if (browser !== 'chrome' && browser !== 'chromium') fail('--browser must be chrome or chromium.');
const waitMs = Number(args.wait) * 1000;
const meetUrl = new URL(args.url ?? fail("--url is needed: the Meet link of the owner's test call."));
if (meetUrl.host !== 'meet.google.com' && meetUrl.hostname !== '127.0.0.1') fail('--url must be a meet.google.com link.');
meetUrl.searchParams.set('hl', 'en');
const code = meetUrl.pathname.slice(1);
if (/^[a-z]{3}-?[a-z]{4}-?[a-z]{3}$/i.test(code)) redactAlso(code, '<code>', false);
if (ownerRuns.includes(run) && !process.stdin.isTTY) {
  fail(`--run ${run} needs the owner at this terminal: it asks them to press Enter.`);
}
if (args.guest && !args.profile) fail('--guest needs --profile: a saved login that was signed out by hand.');
let userDataDir: string;
if (args.profile) {
  userDataDir = profilePath(args.profile);
  if (!fs.existsSync(userDataDir)) fail(`No saved login at .auth/${args.profile}: sign in by hand first (README.md).`);
  if (!env.BOT_ACCOUNT_NAME || !env.BOT_ACCOUNT_EMAIL) {
    fail('Set BOT_ACCOUNT_NAME and BOT_ACCOUNT_EMAIL in .env first, so that logs and screenshots leave them out.');
  }
} else {
  userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 't0.2-anonymous-'));
}

const mode = args.guest ? 'guest-profile' : args.profile ? 'signed-in' : 'anonymous';
const log = new RunLog(`${run}-${browser}${args.headless ? '-headless' : ''}-${mode}`);
const owner = new Owner(log);
const quit = owner.quit.signal;
owner.stopOnCtrlC();
log.line('run.start', {
  run,
  about: runs[run],
  browser,
  headless: args.headless,
  profile: args.profile ? `.auth/${args.profile}${args.guest ? ', signed out, as a guest' : ''}` : 'none (anonymous)',
  network: args.network ?? 'not given',
  ...versions(),
});
if (args.profile) log.line('profile', { created: fs.statSync(userDataDir).birthtime.toISOString() });

const context = await launch(browser, userDataDir, args.headless).catch((error) => {
  log.line('run.end', { result: `could not start ${browser}: ${errorText(error)}` });
  return fail('Is a browser still open on this profile? Quit it, then try again.');
});
log.line('browser', { version: context.browser()?.version() });
const page = context.pages()[0] ?? (await context.newPage());
const watcher = new Watcher(page, log, meetState);
let result = 'unfinished';
try {
  result = await runOnce();
} catch (error) {
  result = `error: ${errorText(error)}`;
} finally {
  watcher.stop();
  await context.close().catch(() => {});
  log.line('screenshots', coverScreenshots(log.dir));
  log.line('run.end', { result, states: watcher.history.map((h) => h.state).join(' > ') });
  owner.close();
  if (!args.profile) fs.rmSync(userDataDir, { recursive: true, force: true });
}

async function runOnce(): Promise<string> {
  log.line('goto', { url: meetUrl.href });
  await page.goto(meetUrl.href, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  log.line('navigator', await navigatorFacts(page));
  watcher.start();

  const lobby = await watcher.waitFor(['lobby', ...endStates], 60_000, quit);
  if (lobby?.state !== 'lobby') return finish(`no pre-join page (${lobby?.state ?? watcher.state})`);
  await sleep(2_000, quit);

  const flags = watcher.flags!;
  const signedIn = !flags.nameInput;
  log.line('login', {
    expected: args.profile && !args.guest ? 'signed in' : 'signed out',
    seen: signedIn ? 'signed in' : 'signed out',
    accountButton: flags.accountButton,
    nameInput: flags.nameInput,
    signIn: flags.signIn,
  });
  if (args.profile && !args.guest && !signedIn) return finish('the saved login did not load');
  if (args.guest && signedIn) return finish('the profile is still signed in: sign out by hand first (README.md)');

  if (!args.profile || args.guest) await typeName();
  await turnOff('microphone');
  await turnOff('camera');
  await sleep(1_000, quit);
  const clicked = await clickJoin();

  const next = await watcher.waitFor(['waiting', 'in-call', ...endStates], 20_000, quit, clicked);
  let state = next?.state ?? watcher.state;
  if (!next) {
    log.line('no-signal', { after: 'click.join', ms: 20_000, state });
    if (state === 'unknown') state = 'waiting';
  } else if (state === 'waiting') {
    measure('click.join', clicked, next);
  }
  if (quit.aborted) return 'stopped by the owner';
  if (endStates.includes(state)) return finish(state);
  if (state === 'lobby') return finish('still on the pre-join page after the join click');
  if (state === 'in-call') log.line('note', { text: 'in the call without being let in' });

  switch (run) {
    case 'ask':
    case 'bot-withdraws': {
      if (run === 'bot-withdraws') owner.say(`Do nothing. The bot cancels its request in ${args.wait} s.`);
      await sleep(waitMs, quit);
      if (quit.aborted) return 'stopped by the owner';
      const inCall = watcher.state === 'in-call';
      const t = inCall ? await leave() : await withdraw();
      const after = await watcher.waitFor([...endStates, 'lobby'], 15_000, quit, t);
      await sleep(10_000, quit);
      if (run === 'bot-withdraws') await owner.ask("Is the bot's request gone from your screen? (y/n)");
      return `${inCall ? 'in the call, so left' : 'withdrew'}, then ${after?.state ?? watcher.state}`;
    }
    case 'let-in': {
      if (!(await letIn(state))) return finish(watcher.state);
      await sleep(5_000, quit);
      await owner.ask(
        args.profile && !args.guest
          ? "Is the bot shown in your call under the account's name? (y/n, or type the name you see)"
          : 'Type the name the bot shows in your call, and any words Meet shows under it.',
      );
      await sleep(15_000, quit);
      if (quit.aborted) return 'stopped by the owner';
      const t = await leave();
      const after = await watcher.waitFor(endStates, 15_000, quit, t);
      return finish(`let in, then left (${after?.state ?? watcher.state})`);
    }
    case 'refused': {
      const { shown, pressed } = owner.enter('Refuse the bot: click Deny entry. Press Enter here at the moment you click.');
      const hit = await watcher.waitFor([...endStates, 'in-call'], 5 * 60_000, quit, shown);
      await measureOwner(pressed, hit);
      return finish(hit?.state ?? watcher.state);
    }
    case 'removed': {
      if (!(await letIn(state))) return finish(watcher.state);
      await sleep(5_000, quit);
      const { shown, pressed } = owner.enter(
        "Remove the bot from the call: open People, then the bot's menu, then remove it. Press Enter here at the moment of your last click.",
      );
      const hit = await watcher.waitFor(endStates, 5 * 60_000, quit, shown);
      await measureOwner(pressed, hit);
      return finish(hit?.state ?? watcher.state);
    }
    case 'call-ended': {
      if (!(await letIn(state))) return finish(watcher.state);
      await sleep(5_000, quit);
      const { shown, pressed } = owner.enter(
        'End the call for everyone, if Meet offers that to you. If it does not, leave the call, and have everyone else leave too. Press Enter here at the moment of your last click.',
      );
      const how = pressed.then(() => owner.ask('Did you end the call for everyone (e), or did everyone leave (l)?'));
      const hit = await watcher.waitFor(endStates, 10 * 60_000, quit, shown);
      await measureOwner(pressed, hit);
      await within(how, 60_000);
      return finish(hit?.state ?? `no end state (${watcher.state})`);
    }
    case 'bot-leaves': {
      if (!(await letIn(state))) return finish(watcher.state);
      await sleep(5_000, quit);
      const { pressed } = owner.enter('Press Enter here to make the bot leave the call.');
      if (Number.isNaN(await pressed)) return 'stopped by the owner';
      const t = await leave();
      const hit = await watcher.waitFor(endStates, 30_000, quit, t);
      if (hit) measure('click.leave', t, hit);
      await sleep(10_000, quit);
      await owner.ask('Is the bot gone from the People list of your call? (y/n)');
      return hit?.state ?? watcher.state;
    }
    case 'no-answer': {
      owner.say('Do nothing: leave the request unanswered until Meet gives up. This can take many minutes.');
      const hit = await watcher.waitFor([...endStates, 'in-call'], 30 * 60_000, quit, clicked);
      if (hit) {
        measure('click.join', clicked, hit);
        if (next) measure('waiting', next.t, hit);
      }
      return finish(hit?.state ?? `Meet did not give up within 30 min (${watcher.state})`);
    }
  }
  return 'unknown run';
}

async function finish(result: string): Promise<string> {
  await sleep(10_000, quit);
  return result;
}

function measure(from: string, t: number, hit: { state: string; t: number }) {
  log.line('measure', { from, to: hit.state, ms: Math.round(hit.t - t) });
}

async function measureOwner(pressed: Promise<number>, hit: { state: string; t: number } | null) {
  const t = await within(pressed, 15_000);
  if (hit && t !== undefined && !Number.isNaN(t)) measure('owner.enter', t, hit);
  else log.line('measure', { from: 'owner.enter', to: hit?.state ?? 'nothing', ms: null });
}

async function letIn(state: string): Promise<boolean> {
  if (state === 'in-call') return true;
  const { shown, pressed } = owner.enter('Let the bot in: click Admit. Press Enter here at the moment you click.');
  const hit = await watcher.waitFor(['in-call', ...endStates], 5 * 60_000, quit, shown);
  await measureOwner(pressed, hit);
  return hit?.state === 'in-call';
}

async function typeName() {
  const box = page.getByRole('textbox', { name: /name/i }).or(page.getByPlaceholder(/name/i)).first();
  await box.fill(args.name, { timeout: 15_000 });
  log.line('fill.name', { name: args.name });
}

async function turnOff(device: 'microphone' | 'camera') {
  const off = page.getByRole('button', { name: new RegExp(`^turn off ${device}`, 'i') }).first();
  const on = page.getByRole('button', { name: new RegExp(`^turn on ${device}`, 'i') }).first();
  try {
    await off.click({ timeout: 15_000 });
    await on.waitFor({ timeout: 5_000 });
    log.line(`${device}.off`);
  } catch (error) {
    log.line(`${device}.off.failed`, { error: errorText(error), offAlready: await on.isVisible().catch(() => false) });
  }
}

async function clickJoin(): Promise<number> {
  const join = page
    .getByRole('button', { name: /^(ask to join|ask to join anyway|join now|join the call now|join anyway|join here too|switch here)$/i })
    .first();
  await join.waitFor({ timeout: 30_000 });
  const button = (await join.innerText()).trim();
  await join.click({ timeout: 10_000 });
  return log.line('click.join', { button });
}

async function leave(): Promise<number> {
  await page.getByRole('button', { name: /^leave call$/i }).first().click({ timeout: 10_000 });
  return log.line('click.leave');
}

async function withdraw(): Promise<number> {
  for (const name of [/^cancel( request)?$/i, /^leave call$/i]) {
    const button = page.getByRole('button', { name }).first();
    if (await button.isVisible()) {
      const label = (await button.getAttribute('aria-label')) || (await button.innerText()).trim();
      await button.click({ timeout: 10_000 });
      return log.line('click.withdraw', { button: label });
    }
  }
  const t = log.line('click.withdraw', { button: 'none found, so the bot closes the page' });
  await page.close();
  return t;
}
