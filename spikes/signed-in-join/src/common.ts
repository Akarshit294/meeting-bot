import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { parseEnv } from 'node:util';
import { chromium, type BrowserContext, type Locator, type Page } from 'playwright';

export const spikeDir = path.resolve(import.meta.dirname, '..');
export const repoDir = path.resolve(spikeDir, '../..');
export const authDir = path.join(repoDir, '.auth');
export const capturesDir = path.join(repoDir, 'captures', 'T0.2');

const envPath = path.join(spikeDir, '.env');
export const env: Record<string, string | undefined> = fs.existsSync(envPath)
  ? parseEnv(fs.readFileSync(envPath, 'utf8'))
  : {};

export function fail(message: string): never {
  console.error(message);
  process.exit(1);
}

export const errorText = (error: unknown) =>
  (error instanceof Error ? error.message : String(error)).split('\n')[0];

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => (clearTimeout(timer), resolve()), { once: true });
  });
}

export function within<T>(promise: Promise<T>, ms: number): Promise<T | undefined> {
  return Promise.race([promise, sleep(ms).then(() => undefined)]);
}

const redactions: Array<[RegExp, string]> = [];
const coveredTexts: string[] = [];

export function redactAlso(value: string | undefined, label: string, cover = true) {
  const text = value?.trim() ?? '';
  if (text.length < 3) return;
  const pattern = text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  redactions.push([new RegExp(pattern, 'gi'), label]);
  if (cover) coveredTexts.push(text);
}

redactAlso(env.BOT_ACCOUNT_PASSWORD, '<password>');
redactAlso(env.BOT_ACCOUNT_EMAIL, "<the bot's account>");
redactAlso(env.BOT_ACCOUNT_NAME, "<the account's name>");
(env.BOT_ACCOUNT_OTHER_NAMES ?? '').split(',').forEach((name) => redactAlso(name, "<the account's name>"));
redactAlso(env.HOST_NAME, '<the host>');
(env.GUEST_NAMES ?? '').split(',').forEach((name, i) => redactAlso(name, `<guest ${i + 1}>`));

export function redact(text: string): string {
  let out = text;
  for (const [pattern, label] of redactions) out = out.replace(pattern, label);
  return out
    .replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, '<email>')
    .replace(/(?<![a-z-])(?:[a-z]{3}-[a-z]{4}-[a-z]{3})+(?![a-z-])/g, '<code>')
    .replaceAll(os.homedir(), '~');
}

function covers(page: Page): Locator[] {
  return [
    page.locator(
      '[aria-label^="Google Account"], input[type="email"], input[type="password"], input[aria-label="Email or phone"], input[name="identifier"]',
    ),
    ...coveredTexts.map((text) => page.getByText(text)),
  ];
}

const screenshotStyle = '[data-participant-id] { filter: blur(16px) !important; }';

async function coverBoxes(page: Page): Promise<string> {
  const boxes = await Promise.all(
    covers(page).map((locator) =>
      locator.evaluateAll((els) =>
        els.map((el) => {
          const r = el.getBoundingClientRect();
          return [r.x, r.y, r.width, r.height].map(Math.round).join(',');
        }),
      ),
    ),
  );
  return boxes.flat().join(' ');
}

const coverSource = path.join(spikeDir, 'src', 'cover.swift');
const coverBinary = path.join(spikeDir, 'node_modules', '.cache', 't0.2-cover');

export function coverScreenshots(target: string, check = false): { ok: boolean; lines: string[] } {
  if (process.platform !== 'darwin') return { ok: false, lines: ['not checked: this needs macOS text recognition'] };
  if (!fs.existsSync(coverBinary) || fs.statSync(coverBinary).mtimeMs < fs.statSync(coverSource).mtimeMs) {
    fs.mkdirSync(path.dirname(coverBinary), { recursive: true });
    const built = spawnSync('swiftc', ['-O', coverSource, '-o', coverBinary], { encoding: 'utf8' });
    if (built.status !== 0) return { ok: false, lines: [`not checked: swiftc failed: ${built.stderr.split('\n')[0]}`] };
  }
  let failed = { ok: false, lines: [] as string[] };
  for (let attempt = 1; attempt <= 2; attempt++) {
    const run = spawnSync(coverBinary, [...(check ? ['--check'] : []), target], { cwd: spikeDir, encoding: 'utf8' });
    const output = `${run.stdout ?? ''}${run.stderr ?? ''}`.trim();
    const lines = output.split('\n').map((line) => line.replace(`${capturesDir}/`, ''));
    if (run.status === 0 || (check && run.status === 2)) return { ok: run.status === 0, lines };
    failed = { ok: false, lines: [...lines, `failed: status ${run.status}, signal ${run.signal}, ${run.error?.message ?? 'no error'}`] };
  }
  return failed;
}

const now = () => performance.timeOrigin + performance.now();

export class RunLog {
  readonly dir: string;
  readonly t0 = now();
  private readonly file: string;
  private captures = 0;

  constructor(name: string) {
    const stamp = new Date().toISOString().replace(/[-:]|\.\d+/g, '');
    this.dir = path.join(capturesDir, `${stamp}-${name}`);
    fs.mkdirSync(this.dir, { recursive: true });
    this.file = path.join(this.dir, 'run.log');
  }

  line(event: string, details?: object): number {
    const t = now();
    const ms = String(Math.round(t - this.t0)).padStart(7);
    const text = redact(`${new Date(t).toISOString()} ${ms} ms  ${event}${details ? `  ${JSON.stringify(details)}` : ''}`);
    fs.appendFileSync(this.file, `${text}\n`);
    console.log(text);
    return t;
  }

  async capture(page: Page, label: string, dump: object): Promise<string> {
    const name = `${String(++this.captures).padStart(2, '0')}-${label.replace(/[^\w.-]+/g, '-')}`;
    fs.writeFileSync(path.join(this.dir, `${name}.json`), redact(JSON.stringify(dump, null, 2)));
    const file = path.join(this.dir, `${name}.png`);
    try {
      for (let attempt = 1; attempt <= 3; attempt++) {
        const before = await coverBoxes(page);
        await page.screenshot({ path: file, scale: 'css', mask: covers(page), style: screenshotStyle, timeout: 10_000 });
        if ((await coverBoxes(page)) === before) return `${name}.png`;
      }
      fs.rmSync(file, { force: true });
      return `${name}.json (no screenshot: the page kept moving)`;
    } catch (error) {
      fs.rmSync(file, { force: true });
      return `${name}.json (no screenshot: ${errorText(error)})`;
    }
  }
}

export class Owner {
  readonly quit = new AbortController();
  private readonly log: RunLog;
  private readonly rl = process.stdin.isTTY ? readline.createInterface({ input: process.stdin }) : null;
  private waiting: ((line: string) => void) | null = null;

  constructor(log: RunLog) {
    this.log = log;
    this.rl?.on('line', (input) => {
      const line = input.trim();
      if (line === 'q') {
        this.log.line('owner.quit');
        this.quit.abort();
        return;
      }
      const waiting = this.waiting;
      this.waiting = null;
      if (waiting) waiting(line);
      else this.log.line('owner.typed', { text: line });
    });
    this.quit.signal.addEventListener('abort', () => this.waiting?.(''));
  }

  say(text: string): number {
    const t = this.log.line('owner.instruction', { text });
    console.log(`\n>>> ${text}\n`);
    return t;
  }

  enter(text: string): { shown: number; pressed: Promise<number> } {
    const shown = this.say(`${text} (q ends the run)`);
    const pressed = new Promise<number>((resolve) => {
      if (!this.rl || this.quit.signal.aborted) return resolve(NaN);
      this.waiting = (line) => resolve(this.quit.signal.aborted ? NaN : this.log.line('owner.enter', line ? { typed: line } : undefined));
    });
    return { shown, pressed };
  }

  async ask(question: string): Promise<string> {
    this.say(question);
    const answer = await new Promise<string>((resolve) => {
      if (!this.rl || this.quit.signal.aborted) return resolve('');
      this.waiting = resolve;
    });
    this.log.line('owner.answer', { question, answer });
    return answer;
  }

  close() {
    this.rl?.close();
  }

  stopOnCtrlC() {
    process.on('SIGINT', () => {
      if (this.quit.signal.aborted) process.exit(130);
      this.log.line('owner.ctrl-c');
      this.quit.abort();
    });
  }
}

export type BrowserName = 'chrome' | 'chromium';

export const extraArgs = [
  '--use-fake-device-for-media-stream',
  '--use-fake-ui-for-media-stream',
  '--lang=en-US',
  '--window-size=1280,800',
];

export function launch(browser: BrowserName, userDataDir: string, headless: boolean): Promise<BrowserContext> {
  return chromium.launchPersistentContext(userDataDir, {
    channel: browser === 'chrome' ? 'chrome' : undefined,
    headless,
    args: extraArgs,
    viewport: null,
  });
}

export function profilePath(name: string): string {
  if (!/^[\w.-]+$/.test(name)) fail('--profile takes the name of a folder under .auth/, such as "chrome".');
  return path.join(authDir, name);
}

export function versions() {
  const playwright: string = createRequire(import.meta.url)('playwright/package.json').version;
  const system =
    process.platform === 'darwin'
      ? `macOS ${execFileSync('sw_vers', ['-productVersion']).toString().trim()}`
      : `${os.type()} ${os.release()}`;
  return { node: process.version, playwright, os: `${system} (${os.arch()})` };
}

export function navigatorFacts(page: Page) {
  return page.evaluate(async () => {
    const uaData = (navigator as any).userAgentData;
    const high = uaData ? await uaData.getHighEntropyValues(['fullVersionList', 'platformVersion']) : {};
    return {
      webdriver: navigator.webdriver,
      userAgent: navigator.userAgent,
      brands: high.fullVersionList?.map((b: any) => `${b.brand} ${b.version}`).join(', '),
      platform: uaData ? `${uaData.platform} ${high.platformVersion}` : undefined,
      languages: navigator.languages.join(', '),
    };
  });
}
