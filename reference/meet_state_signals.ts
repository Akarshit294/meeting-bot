import type { Page } from 'playwright';
import { errorText, redact, sleep, type RunLog } from './common.ts';

export interface Flags {
  url: string;
  title: string;
  heading: string;
  nameInput: boolean;
  joinButton: string;
  accountButton: boolean;
  signIn: boolean;
  micOff: boolean;
  cameraOff: boolean;
  waiting: string;
  joined: string;
  leaveCall: boolean;
  shareScreen: boolean;
  captions: boolean;
  chat: boolean;
  people: string;
  tiles: number;
  selfName: boolean;
  audio: number;
  end: string;
}

export interface Snapshot {
  flags: Flags;
  items: string[];
}

export function readPage(): Snapshot {
  const norm = (s: string | null | undefined) =>
    (s ?? '').replace(/[‘’]/g, "'").replace(/\s+/g, ' ').trim();
  const shown = (el: Element) => el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const items = new Set<string>();
  const add = (kind: string, text: string) => {
    const t = norm(text).slice(0, 160);
    if (t && !/^\d{1,2}:\d{2}(:\d{2})?(\s?[AP]M)?$/i.test(t)) items.add(`${kind}: ${t}`);
  };

  const bodyText = document.body?.innerText ?? '';
  for (const line of bodyText.split('\n')) add('text', line);
  const buttons = [...document.querySelectorAll('button, [role="button"]')].filter(shown).map((el) => ({
    label: norm(el.getAttribute('aria-label')),
    text: norm((el as HTMLElement).innerText),
  }));
  for (const b of buttons) add('button', b.label && b.text && b.label !== b.text ? `${b.label} | ${b.text}` : b.label || b.text);
  const links = [...document.querySelectorAll('a')].filter(shown).map((el) => norm(el.innerText || el.getAttribute('aria-label')));
  for (const link of links) add('link', link);
  const inputs = [...document.querySelectorAll('input, textarea')].filter(shown).map((el) =>
    ['type', 'aria-label', 'placeholder'].map((name) => norm(el.getAttribute(name))).filter(Boolean).join(' | '),
  );
  for (const input of inputs) add('input', input);
  for (const el of document.querySelectorAll('[role="dialog"], [role="alertdialog"], [role="alert"], [role="status"], [aria-live]')) {
    if (shown(el)) add(`live ${el.getAttribute('role') ?? el.getAttribute('aria-live')}`, (el as HTMLElement).innerText);
  }
  for (const el of document.querySelectorAll('[data-participant-id]')) {
    add('tile', `${el.getAttribute('aria-label') ?? ''} ${(el as HTMLElement).innerText.split('\n')[0] ?? ''}`);
  }

  const body = norm(bodyText);
  const find = (re: RegExp) => body.match(re)?.[0] ?? '';
  const button = (re: RegExp) => buttons.find((b) => re.test(b.label) || re.test(b.text));
  const join = button(/^(ask to join|ask to join anyway|join now|join the call now|join anyway|join here too|switch here)$/i);
  const ends: Array<[string, RegExp]> = [
    ['blocked', /you can't join this (video call|meeting)|there is a problem connecting to this video call/i],
    ['not-found', /check your meeting code|invalid video call name|meeting code has expired|meeting code you entered doesn't work/i],
    ['refused', /denied your request|your request to join was denied|you were denied|weren't allowed to join|not allowed to join|not admitted/i],
    ['no-answer', /no one (has )?responded to your request|nobody responded/i],
    ['removed', /you've been removed from the (meeting|call)|removed you from the (meeting|call)|you have been removed/i],
    ['ended', /the call ended because everyone left|ended the (meeting|call) for everyone|the (call|meeting) has ended|(call|meeting) ended/i],
    ['left', /you left the (meeting|call)|you've left the (meeting|call)/i],
  ];
  let end = '';
  for (const [state, re] of ends) {
    const text = find(re);
    if (text) {
      end = `${state}: ${text}`;
      break;
    }
  }

  return {
    flags: {
      url: location.host + location.pathname,
      title: norm(document.title),
      heading: norm((document.querySelector('h1, [role="heading"]') as HTMLElement | null)?.innerText).slice(0, 120),
      nameInput: inputs.some((input) => /name/i.test(input)),
      joinButton: join ? join.text || join.label : '',
      accountButton: [...document.querySelectorAll('[aria-label^="Google Account"]')].some(shown),
      signIn: [...buttons.map((b) => b.text), ...links].some((text) => /^sign in$/i.test(text)),
      micOff: !!button(/^turn on microphone/i),
      cameraOff: !!button(/^turn on camera/i),
      waiting: find(
        /asking to be let in|asking to join|you'll join the call when someone lets you in|please wait until a meeting host brings you into the call|waiting for the host to let you in|you're in the waiting room|someone will let you in/i,
      ),
      joined: find(/you have joined the call/i),
      leaveCall: !!button(/^leave call$/i),
      shareScreen: !!button(/share screen|present now/i),
      captions: !!button(/captions/i),
      chat: !!button(/chat with everyone/i),
      people: button(/^people\b|show everyone|participants/i)?.label ?? '',
      tiles: document.querySelectorAll('[data-participant-id]').length,
      selfName: document.querySelector('[data-self-name]') !== null,
      audio: document.querySelectorAll('audio').length,
      end,
    },
    items: [...items],
  };
}

export const endStates = ['blocked', 'not-found', 'refused', 'no-answer', 'removed', 'ended', 'left'];

export function meetState(flags: Flags): string {
  if (flags.end) return flags.end.slice(0, flags.end.indexOf(':'));
  if (flags.shareScreen || flags.captions) return 'in-call';
  if (flags.waiting) return 'waiting';
  if (flags.joinButton || flags.nameInput) return 'lobby';
  return 'unknown';
}

export class Watcher {
  state = 'start';
  flags: Flags | null = null;
  readonly history: Array<{ state: string; t: number }> = [];
  private readonly page: Page;
  private readonly log: RunLog;
  private readonly classify: (flags: Flags) => string;
  private items = new Set<string>();
  private running = false;
  private lastCapture = 0;

  constructor(page: Page, log: RunLog, classify: (flags: Flags) => string) {
    this.page = page;
    this.log = log;
    this.classify = classify;
  }

  start() {
    this.running = true;
    void this.loop();
  }

  stop() {
    this.running = false;
  }

  private async loop() {
    while (this.running && !this.page.isClosed()) {
      try {
        await this.tick();
      } catch (error) {
        this.log.line('watch.error', { error: errorText(error) });
      }
      await sleep(250);
    }
  }

  private async tick() {
    const snapshot = await this.page.evaluate(readPage).catch(() => null);
    if (!snapshot || !this.running) return;
    const items = new Set(snapshot.items.map(redact));
    const added = [...items].filter((item) => !this.items.has(item));
    const removed = [...this.items].filter((item) => !items.has(item));
    this.items = items;
    if (added.length || removed.length) this.log.line('page', { added, removed });

    const flags: Flags = JSON.parse(redact(JSON.stringify(snapshot.flags)));
    const keys = Object.keys(flags) as Array<keyof Flags>;
    const changed = keys.filter((key) => flags[key] !== this.flags?.[key]);
    this.flags = flags;
    const state = this.classify(flags);
    if (state !== this.state) {
      const t = this.log.line('state', { from: this.state, to: state, flags });
      this.state = state;
      this.history.push({ state, t });
      await this.capture(state, flags, items);
    } else if (changed.length) {
      this.log.line('signals', Object.fromEntries(changed.map((key) => [key, flags[key]])));
      if (performance.now() - this.lastCapture > 2000) await this.capture(state, flags, items);
    }
  }

  private async capture(state: string, flags: Flags, items: Set<string>) {
    this.lastCapture = performance.now();
    const file = await this.log.capture(this.page, state, { state, flags, items: [...items] });
    this.log.line('capture', { file });
  }

  async waitFor(states: string[], timeoutMs: number, signal: AbortSignal, since = 0) {
    const end = performance.now() + timeoutMs;
    while (!signal.aborted && !this.page.isClosed()) {
      const hit = this.history.find((h) => h.t >= since && states.includes(h.state));
      if (hit) return hit;
      if (performance.now() > end) return null;
      await sleep(100, signal);
    }
    return null;
  }
}
