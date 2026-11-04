import fs from 'node:fs';
import path from 'node:path';
import { capturesDir, coverScreenshots, env, repoDir, spikeDir } from './common.ts';

const checks: Array<{ label: string; pattern: RegExp }> = [];
const add = (label: string, value: string | undefined) => {
  const text = value?.trim() ?? '';
  if (text.length >= 3) checks.push({ label, pattern: new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi') });
};
add('BOT_ACCOUNT_PASSWORD', env.BOT_ACCOUNT_PASSWORD);
add('BOT_ACCOUNT_EMAIL', env.BOT_ACCOUNT_EMAIL);
add('the part of BOT_ACCOUNT_EMAIL before the @', env.BOT_ACCOUNT_EMAIL?.split('@')[0]);
add('BOT_ACCOUNT_NAME', env.BOT_ACCOUNT_NAME);
(env.BOT_ACCOUNT_OTHER_NAMES ?? '').split(',').forEach((name, i) => add(`BOT_ACCOUNT_OTHER_NAMES item ${i + 1}`, name));
(env.BOT_ACCOUNT_NAME ?? '').split(/\s+/).forEach((word, i) => add(`word ${i + 1} of BOT_ACCOUNT_NAME`, word));
if (!checks.length) {
  console.error('Nothing to check: .env has none of BOT_ACCOUNT_NAME, BOT_ACCOUNT_EMAIL, BOT_ACCOUNT_PASSWORD.');
  process.exit(1);
}

function textFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : textFiles(file);
    return entry.name !== '.env' && /\.(log|json|md|ts)$|^\.env\.example$/.test(entry.name) ? [file] : [];
  });
}

const blank = (text: string) => checks.reduce((out, { label, pattern }) => out.replace(pattern, `<${label}>`), text);
const files = [...textFiles(capturesDir), ...textFiles(path.join(repoDir, 'docs')), ...textFiles(spikeDir)];
console.log(`Checked ${files.length} files in captures/T0.2/, docs/ and spikes/T0.2-join/.`);
let found = 0;
for (const { label, pattern } of checks) {
  const hits = files.flatMap((file) => {
    const text = fs.readFileSync(file, 'utf8');
    return [...text.matchAll(pattern)].map((match) => ({ file, text, at: match.index }));
  });
  found += hits.length;
  console.log(`${label}: ${hits.length} found`);
  for (const { file, text, at } of hits.slice(0, 5)) {
    const around = blank(text.slice(Math.max(0, at - 50), at + 80)).replace(/\s+/g, ' ');
    console.log(`  ${path.relative(repoDir, file)}: ...${around}...`);
  }
}
console.log('Screenshots in captures/T0.2/, read with macOS text recognition:');
const screenshots = coverScreenshots(capturesDir, true);
for (const line of screenshots.lines) console.log(`  ${line}`);
process.exit(found || !screenshots.ok ? 2 : 0);
