import * as fs from 'node:fs';
import type { DisplaySnapshot } from './types.ts';
import { render } from './render.ts';
import { validate, project, weeklyData, weeklyFromDatabase, enrichFromDatabase } from './data.ts';
import { PrivateTree, HASH, hash, jsonBytes, parseJson, object, missing, privateInput, canonical } from './private-store.ts';

export const ROUTE = /^\/[a-z0-9][a-z0-9-]{0,79}\/$/;
interface Index { schema_version: 'trading-review-publications.v1'; current: string; previous: string | null; routes: Record<string, string> }
const initial = (): Index => ({ schema_version: 'trading-review-publications.v1', current: '', previous: null, routes: {} });
export const sourceTimes = (s: DisplaySnapshot) => ({ review_date: s.daily.meta.review_date, content_generated_at: s.daily.meta.generated_at, market_as_of: s.daily.meta.market_as_of, weekly_generated_at: s.weekly?.meta.generated_at ?? null });
export interface Manifest { schema_version: 'trading-review-publication.v1'; publication_id: string; view_sha256: string; html_sha256: string; source_times: ReturnType<typeof sourceTimes> }
type CalendarEvent = DisplaySnapshot['daily']['events']['groups'][number]['events'][number];
type CalendarCoverage = NonNullable<DisplaySnapshot['daily']['events']['coverage']>[number];
type CalendarScope = 'macro' | 'fed_speech' | 'earnings';
const REQUIRED_CALENDAR_SCOPES: CalendarScope[] = ['macro', 'fed_speech', 'earnings'];
const NY_DATE = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });

function newYorkDate(value: string): string {
  const parts = Object.fromEntries(NY_DATE.formatToParts(new Date(value)).filter(p => p.type !== 'literal').map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
function dayAdd(value: string, days: number): string {
  const date = new Date(`${value}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + days); return date.toISOString().slice(0, 10);
}
function calendarWindow(reference: string): { start: string; end: string } {
  const date = newYorkDate(reference), weekday = (new Date(`${date}T12:00:00Z`).getUTCDay() + 6) % 7;
  const start = dayAdd(date, -weekday); return { start, end: dayAdd(start, 14) };
}
function coverageScope(row: CalendarCoverage): CalendarScope | null {
  if (/联储.*讲话|美联储|地区联储/.test(row.label)) return 'fed_speech';
  if (/宏观/.test(row.label)) return 'macro';
  if (/财报/.test(row.label)) return 'earnings';
  return null;
}
function eventKey(row: CalendarEvent): string {
  return `${row.et_date}|${row.et_time.slice(0, 5)}|${row.title.trim().toLowerCase()}`;
}
function calendarRows(view: DisplaySnapshot): CalendarEvent[] {
  return view.daily.events.groups.flatMap(group => group.events);
}
function assertCombinedCalendar(view: DisplaySnapshot): void {
  const events = view.daily.events;
  if (!events.reference_at) return;
  const scopes = new Set((events.coverage ?? []).map(coverageScope).filter((scope): scope is CalendarScope => scope !== null));
  if (REQUIRED_CALENDAR_SCOPES.some(scope => !scopes.has(scope))) throw new Error('event_calendar_coverage_incomplete');
}
function assertCalendarProgress(old: DisplaySnapshot, next: DisplaySnapshot): void {
  const previous = old.daily.events, current = next.daily.events;
  if (!previous.reference_at) return;
  if (!current.reference_at) throw new Error('event_calendar_reference_regression');
  if (Date.parse(current.reference_at) < Date.parse(previous.reference_at)) throw new Error('event_calendar_reference_regression');
  const window = calendarWindow(current.reference_at);
  const currentKeys = new Set(calendarRows(next).map(eventKey));
  const missing = calendarRows(old).filter(row => row.et_date >= window.start && row.et_date < window.end && !currentKeys.has(eventKey(row)));
  if (missing.length) throw new Error('event_calendar_item_regression');
}
function indexValue(value: unknown): Index {
  const o = object(value, ['schema_version', 'current', 'previous', 'routes']);
  if (o.schema_version !== 'trading-review-publications.v1' || typeof o.current !== 'string' || !HASH.test(o.current) || (o.previous !== null && (typeof o.previous !== 'string' || !HASH.test(o.previous))) || !o.routes || typeof o.routes !== 'object' || Array.isArray(o.routes)) throw new Error('invalid_publication_index');
  for (const [r, h] of Object.entries(o.routes)) if (!ROUTE.test(r) || typeof h !== 'string' || !HASH.test(h)) throw new Error('invalid_history_route');
  return o as unknown as Index;
}
export class PublicationStore extends PrivateTree {
  index(): Index { return indexValue(parseJson(this.read('publications.json'))); }
  load(id = this.index().current): { view: DisplaySnapshot; html: Buffer; manifest: Manifest } {
    if (!HASH.test(id)) throw new Error('invalid_publication_identity');
    const base = `publications/${id}`;
    const m = object(parseJson(this.read(`${base}/manifest.json`)), ['schema_version', 'publication_id', 'view_sha256', 'html_sha256', 'source_times']);
    const v = this.read(`${base}/view.json`), html = this.read(`${base}/index.html`);
    if (m.schema_version !== 'trading-review-publication.v1' || m.publication_id !== id || hash(v) !== m.view_sha256 || hash(html) !== m.html_sha256 || hash(Buffer.concat([v, Buffer.from([0]), html])) !== id) throw new Error('publication_integrity_failed');
    const raw = object(parseJson(v), ['schema_version', 'daily', 'weekly']);
    if (raw.schema_version !== 'trading-review-display.v1') throw new Error('invalid_saved_view');
    const view = raw as unknown as DisplaySnapshot;
    if (canonical(sourceTimes(view)) !== canonical(m.source_times)) throw new Error('source_time_readback_failed');
    return { view, html, manifest: m as unknown as Manifest };
  }
  publish(input: unknown, options: { route?: string; expectedCurrent?: string | null } = {}): Manifest {
    const view = validate(input);
    assertCombinedCalendar(view);
    if (options.route !== undefined && !ROUTE.test(options.route)) throw new Error('invalid_history_route');
    const encoded = jsonBytes(view), html = Buffer.from(render(view));
    const id = hash(Buffer.concat([encoded, Buffer.from([0]), html]));
    const manifest: Manifest = { schema_version: 'trading-review-publication.v1', publication_id: id, view_sha256: hash(encoded), html_sha256: hash(html), source_times: sourceTimes(view) };
    return this.lock('publish', () => {
      let index: Index;
      try { index = this.index(); } catch (e) { if (!missing(e) || fs.existsSync(this.root + '/publications.json')) throw e; index = initial(); }
      const current = index.current || null;
      if ('expectedCurrent' in options && options.expectedCurrent !== current) throw new Error('publication_changed_during_preparation');
      if (options.route && options.route in index.routes && index.routes[options.route] !== id) throw new Error('historical_route_is_immutable');
      if (current) {
        const old = this.load(current).view;
        if (view.daily.meta.review_date < old.daily.meta.review_date || Date.parse(view.daily.meta.generated_at) < Date.parse(old.daily.meta.generated_at)) throw new Error('daily_record_regression');
        if (old.weekly && (!view.weekly || view.weekly.meta.period_end < old.weekly.meta.period_end || Date.parse(view.weekly.meta.generated_at) < Date.parse(old.weekly.meta.generated_at))) throw new Error('weekly_record_regression');
        assertCalendarProgress(old, view);
      }
      for (const [name, content] of [['view.json', encoded], ['index.html', html], ['manifest.json', jsonBytes(manifest)]] as const) this.writeOnce(`publications/${id}/${name}`, content);
      this.load(id);
      if (options.route) index.routes[options.route] = id;
      if (current !== id) { index.previous = current; index.current = id; }
      this.write('publications.json', jsonBytes(indexValue(index)));
      return manifest;
    });
  }
  rollback(): Manifest {
    return this.lock('publish', () => {
      const index = this.index(); if (!index.previous) throw new Error('no_previous_publication');
      const manifest = this.load(index.previous).manifest;
      [index.current, index.previous] = [index.previous, index.current];
      this.write('publications.json', jsonBytes(index)); return manifest;
    });
  }
}
export function prepare(store: PublicationStore, inputs: { dailyInput?: string; displayInput?: string; weeklyInput?: string; weeklyKey?: string; enrichDb?: boolean } = {}): { view: DisplaySnapshot; current: string | null } {
  if (inputs.dailyInput && inputs.displayInput) throw new Error('choose_one_daily_source');
  if (inputs.weeklyInput && inputs.weeklyKey) throw new Error('choose_one_weekly_source');
  let saved: DisplaySnapshot | null = null, current: string | null = null;
  try { current = store.index().current; saved = store.load(current).view; } catch (e) { if (!missing(e) || fs.existsSync(store.root + '/publications.json')) throw e; }
  const supplied = inputs.displayInput ? validate(privateInput(inputs.displayInput)) : null;
  const weekly = inputs.weeklyKey ? weeklyFromDatabase(inputs.weeklyKey) : inputs.weeklyInput ? weeklyData(privateInput(inputs.weeklyInput)) : (supplied ?? saved)?.weekly ?? null;
  let view: DisplaySnapshot;
  if (supplied) view = { ...supplied, weekly };
  else if (inputs.dailyInput) view = project(privateInput(inputs.dailyInput), weekly);
  else if (saved) view = { ...saved, weekly };
  else throw new Error('first_publication_requires_daily_input');
  if (inputs.enrichDb) view = enrichFromDatabase(view);
  return { view: validate(view), current };
}
