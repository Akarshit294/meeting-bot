import fs from 'node:fs';
import path from 'node:path';
import { capturesDir } from './common.ts';

type Items = Set<string>;
const has = (items: Items, prefix: string) => [...items].some((item) => item.startsWith(prefix));
const text = (t: string) => (items: Items) => items.has(`text: ${t}`);
const button = (label: string) => (items: Items) => has(items, `button: ${label}`);

const inCallToolbar = ['Share screen', 'Turn on captions', 'Turn off captions', 'Raise hand', 'Send a reaction', 'Meeting tools', 'Meeting details'];
const states: Array<[string, (items: Items) => boolean]> = [
  ['blocked', text("You can't join this video call")],
  ['refused', text('Someone in the call denied your request to join')],
  ['removed', text("You've been removed from the meeting")],
  ['left', text('You left the meeting')],
  ['in-call', (items) => inCallToolbar.some((label) => has(items, `button: ${label}`))],
  ['waiting', text('Please wait until a meeting host brings you into the call')],
  ['lobby', (items) => button('Ask to join')(items) || button('Join now')(items)],
  ['loading', text('Getting ready...')],
];
const stateOf = (items: Items) => states.find(([, test]) => test(items))?.[0] ?? 'other';

const signals: Array<[string, (items: Items) => boolean]> = [
  ['text "Getting ready..."', text('Getting ready...')],
  ['text "Ready to join?"', text('Ready to join?')],
  ['button "Ask to join"', button('Ask to join')],
  ['button "Join now"', button('Join now')],
  ['a name field', (items) => [...items].some((item) => item.startsWith('input:') && /name/i.test(item))],
  ['button "Sign in"', button('Sign in')],
  ['text "Please wait until a meeting host brings you into the call"', text('Please wait until a meeting host brings you into the call')],
  ['button "Leave call"', button('Leave call')],
  ['button "Chat with everyone"', button('Chat with everyone')],
  ['button "Share screen"', button('Share screen')],
  ['button "Turn on captions" or "Turn off captions"', (items) => button('Turn on captions')(items) || button('Turn off captions')(items)],
  ['button "Raise hand"', button('Raise hand')],
  ['button "Meeting details"', button('Meeting details')],
  ['live region "You have joined the call..."', (items) => [...items].some((item) => item.includes('You have joined the call'))],
  ['at least one participant tile', (items) => has(items, 'tile: ')],
  ['at least two participant tiles', (items) => [...items].filter((item) => item.startsWith('tile: ')).length >= 2],
  ['text "No one else is in this meeting"', text('No one else is in this meeting')],
  ['text "<the host> has left the meeting"', text('<the host> has left the meeting')],
  ['text "Someone in the call denied your request to join"', text('Someone in the call denied your request to join')],
  ['text "You\'ve been removed from the meeting"', text("You've been removed from the meeting")],
  ['text "You left the meeting"', text('You left the meeting')],
  ['button "Rejoin"', button('Rejoin')],
  ['button "Return to home screen"', button('Return to home screen')],
  ['text "You can\'t join this video call"', text("You can't join this video call")],
];

interface Stretch {
  state: string;
  start: string;
  seen: boolean[];
  always: boolean[];
}

const runs = fs
  .readdirSync(capturesDir)
  .filter((name) => /-(ask|let-in|refused|removed|call-ended|bot-leaves|bot-withdraws|no-answer)-/.test(name))
  .sort();
const all: Stretch[] = [];
console.log('## The states of each run\n');
for (const run of runs) {
  const items: Items = new Set();
  const stretches: Stretch[] = [];
  for (const line of fs.readFileSync(path.join(capturesDir, run, 'run.log'), 'utf8').split('\n')) {
    const match = line.match(/^(\S+) +\d+ ms {2}page {2}(.*)$/);
    if (!match) continue;
    const change: { added: string[]; removed: string[] } = JSON.parse(match[2]);
    for (const item of change.removed) items.delete(item);
    for (const item of change.added) items.add(item);
    const state = stateOf(items);
    let current = stretches.at(-1);
    if (current?.state !== state) {
      current = { state, start: match[1], seen: signals.map(() => false), always: signals.map(() => true) };
      stretches.push(current);
    }
    signals.forEach(([, test], i) => {
      const on = test(items);
      current.seen[i] ||= on;
      current.always[i] &&= on;
    });
  }
  console.log(`- ${run}: ${stretches.map((s) => `${s.state} ${s.start.slice(11, 23)}`).join(' > ')}`);
  all.push(...stretches);
}

const columns = ['loading', 'lobby', 'waiting', 'in-call', 'refused', 'removed', 'left', 'blocked'];
const totals = columns.map((state) => all.filter((s) => s.state === state).length);
console.log(`\n## Signals by state\n`);
console.log('Each cell: in how many stretches of that state the signal showed. "*": not for the whole stretch.\n');
console.log(`| Signal | ${columns.map((c, i) => `${c} (${totals[i]})`).join(' | ')} |`);
console.log(`|---|${columns.map(() => '---').join('|')}|`);
signals.forEach(([name], i) => {
  const cells = columns.map((state) => {
    const stretches = all.filter((s) => s.state === state);
    const seen = stretches.filter((s) => s.seen[i]).length;
    const always = stretches.filter((s) => s.always[i]).length;
    return `${seen}${seen > always ? '*' : ''}`;
  });
  console.log(`| ${name} | ${cells.join(' | ')} |`);
});
