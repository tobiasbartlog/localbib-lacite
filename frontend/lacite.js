// lacite — the frontend of the LaCite Bundle (Add-on Contract 2).
//
// One script per Bundle. It holds the LaCite page (/lacite): the tutorial
// that is also the empty state, the connection cards with every state
// (Aktuell, Wird aktualisiert, Pausiert, Fehler) and their list of missing
// cited keys ("Fehlende ansehen …", import through LocalBib.importBibtex),
// the three-step dialog "Neue Verbindung" and the section "KI-Assistent
// verbinden"; and the live session (/lacite/live): what the agent looked up,
// a hit opening the core's PDF viewer beside the log. Layout, copy and
// states follow the approved designs (docs/lacite/design/*.dc.html,
// docs/bib-metadata/design/LaCite*.dc.html), built
// with the core's own classes (lb-view, lb-page-head, lb-key, lb-input,
// lb-tabs, lb-set-card) and the --lb-* tokens; everything of its own is
// `lacite-*` in lacite.css.
//
// UI texts are German and hardcoded, like the other Bundles: an Add-on is
// exempt from the core i18n (CLAUDE.md, "Plugins are exempt"); its locale
// file carries only the nav label. Errors come from the router as codes
// (`error.lacite.*`) and are worded here (LACITE_ERROR_TEXT) — a small fetch
// wrapper keeps the code, which LocalBib.api would turn into a core text.
//
// The pure helpers stay reachable for vitest as window.laCitePure.
(function () {
'use strict';
const LB = window.LocalBib;
if (!LB || typeof LB.registerPlugin !== 'function') return;

const BASE = '/api/plugins/lacite';
const REFRESH_MS = 15000;

const LACITE_ERROR_TEXT = {
    'error.lacite.report_not_found': 'Diesen Bericht gibt es nicht mehr.',
    'error.lacite.folder_required': 'Bitte wähle einen Ordner.',
    'error.lacite.folder_not_absolute': 'Bitte gib den vollständigen Pfad an, z. B. C:\\Users\\…\\Dissertation.',
    'error.lacite.folder_missing': 'Diesen Ordner gibt es nicht (mehr). Wurde er verschoben oder umbenannt?',
    'error.lacite.folder_not_a_folder': 'Das ist eine Datei, kein Ordner.',
    'error.lacite.folder_is_root': 'Ein ganzes Laufwerk geht nicht – wähle den Ordner deines Projekts.',
    'error.lacite.folder_inside_localbib': 'Dieser Ordner gehört LocalBib selbst (Bibliothek, Add-ons oder Einstellungen). Wähle den Ordner deines LaTeX-Projekts.',
    'error.lacite.filename_invalid': 'Der Dateiname muss auf .bib enden und darf keinen Ordner enthalten.',
    'error.lacite.target_taken': 'Diese Datei hält schon eine andere Verbindung aktuell. Wähle einen anderen Dateinamen.',
    'error.lacite.source_invalid': 'Bitte wähle eine Quelle.',
    'error.lacite.collection_missing': 'Diese Sammlung gibt es nicht mehr. Wähle eine andere Quelle.',
    'error.lacite.collection_deleted': 'Die Sammlung wurde gelöscht.',
    'error.lacite.collections_unsupported': 'Diese LocalBib-Version kennt noch keine Sammlungen – nimm die ganze Bibliothek.',
    'error.lacite.foreign_file': 'Die Datei stammt nicht von LaCite.',
    'error.lacite.target_unreadable': 'Die vorhandene Datei ließ sich nicht lesen. Ist sie in einem anderen Programm geöffnet, oder liegt dort ein Ordner mit diesem Namen?',
    'error.lacite.write_failed':'Die Datei ließ sich nicht schreiben. Ist sie in einem anderen Programm geöffnet oder schreibgeschützt?',
    'error.lacite.sync_failed': 'Der Abgleich ist fehlgeschlagen. LaCite versucht es in einer halben Minute wieder.',
    'error.lacite.picker_unavailable': 'Der Ordnerdialog lässt sich hier nicht öffnen. Füge den Pfad stattdessen ein.',
    'error.lacite.picker_busy': 'Der Ordnerdialog ist schon offen – schau hinter dem Browserfenster nach.',
    'error.lacite.open_unavailable': 'Der Ordner lässt sich auf diesem System nicht öffnen.',
    'error.lacite.connection_not_found': 'Diese Verbindung gibt es nicht mehr.',
    'error.lacite.connection_invalid': 'Die Angaben sind unvollständig.',
};

function laciteErrorText(err) {
    const code = err && err.code;
    if (code && LACITE_ERROR_TEXT[code]) return LACITE_ERROR_TEXT[code];
    return 'Das hat nicht geklappt' + (err && err.status ? ' (' + err.status + ')' : '') + '. Bitte versuch es noch einmal.';
}

// fetch against the Bundle's router; a failure rejects with {code, status}.
async function lcApi(path, options = {}) {
    const init = { method: options.method || 'GET', headers: { 'Content-Type': 'application/json' } };
    if (options.body !== undefined) init.body = JSON.stringify(options.body);
    let resp;
    try {
        resp = await fetch(BASE + path, init);
    } catch (e) {
        throw { code: 'network', status: 0 };
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (!resp.ok) {
        const detail = data && data.detail;
        throw { code: detail && typeof detail === 'object' ? detail.code : null, status: resp.status };
    }
    return data;
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

function laciteCount(n) {
    const value = Number(n) || 0;
    return new Intl.NumberFormat('de-DE').format(value) + (value === 1 ? ' Eintrag' : ' Einträge');
}

// "gerade eben" / "vor 3 Min." / "vor 2 Std." / "am 21.10.2026" from an ISO time.
function laciteAgo(iso, now) {
    if (!iso) return '';
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return '';
    const seconds = Math.max(0, Math.round(((now === undefined ? Date.now() : now) - then) / 1000));
    if (seconds < 60) return 'gerade eben';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return 'vor ' + minutes + ' Min.';
    const hours = Math.round(minutes / 60);
    if (hours < 24) return 'vor ' + hours + ' Std.';
    return 'am ' + new Date(then).toLocaleDateString('de-DE');
}

// The card's status pill: label + tone (ok | soft | danger).
function laciteStatus(conn) {
    const st = (conn && conn.status) || {};
    if (st.state === 'ok') return { label: 'Aktuell', tone: 'ok' };
    if (st.state === 'syncing' || st.state === 'new') return { label: 'Wird aktualisiert …', tone: 'soft' };
    if (st.state === 'paused' || st.state === 'foreign') return { label: 'Pausiert', tone: 'danger' };
    return { label: 'Fehler', tone: 'danger' };
}

// "Ganze Bibliothek · 1.284 Einträge" / "Sammlung „X“ · 31 Einträge".
function laciteSourceLabel(conn) {
    const st = (conn && conn.status) || {};
    const source = (conn && conn.source) || {};
    const info = st.source || {};
    const count = st.entries === null || st.entries === undefined ? '' : ' · ' + laciteCount(st.entries);
    if (source.kind !== 'collection') return 'Ganze Bibliothek' + count;
    const name = info.name ? 'Sammlung „' + info.name + '“' : 'Eine Sammlung';
    return info.deleted ? name : name + count;
}

function laciteMcpUrl(origin) {
    return String(origin || '').replace(/\/+$/, '') + BASE + '/mcp';
}

// The copyable setup of each assistant tab, built from the page's origin
// (never a hard-coded port).
function laciteAssistantSnippet(tab, origin) {
    const url = laciteMcpUrl(origin);
    if (tab === 'claude') return 'claude mcp add --transport http --scope user localbib ' + url;
    if (tab === 'opencode') {
        return JSON.stringify({ mcp: { localbib: { type: 'remote', url } } }, null, 2);
    }
    return url;
}

function laciteMissingText(conn) {
    const doc = conn && conn.document;
    const keys = (doc && doc.missing_keys) || [];
    if (!keys.length) return null;
    const shown = keys.slice(0, 8);
    const rest = keys.length - shown.length;
    const where = conn.source && conn.source.kind === 'collection' ? 'in der Sammlung' : 'in deiner Bibliothek';
    const head = keys.length === 1 ? '1 zitierter Schlüssel fehlt ' + where + ': '
        : keys.length + ' zitierte Schlüssel gibt es nicht ' + where + ': ';
    return { head, keys: shown, rest };
}

// The groups of GET connections/{id}/missing, in the card's order.
function laciteMissingGroups(data) {
    const keys = (data && data.keys) || [];
    const of = (status) => keys.filter((k) => k.status === status);
    const found = of('found');
    const files = [];
    for (const k of found) {
        const f = files.find((x) => x.name === k.file);
        if (f) f.count += 1; else files.push({ name: k.file, count: 1 });
    }
    return { found, library: of('library'), notFound: of('not_found'), files };
}

// "aus literatur_alt.bib (19) · quellen/normen.bib (5)"
function laciteFilesLine(files) {
    if (!files.length) return '';
    if (files.length === 1) return 'aus ' + files[0].name;
    return 'aus ' + files.map((f) => f.name + ' (' + f.count + ')').join(' · ');
}

// The name the core's import shows for the handed-over text.
function laciteImportName(groups, conn) {
    if (groups.files.length === 1 && groups.files[0].name) return groups.files[0].name.replace(/^.*[\\/]/, '');
    return 'LaCite – ' + ((conn && conn.name) || 'fehlende Schlüssel') + '.bib';
}

// The line that still includes an old .bib, as the document writes it.
function laciteIncludeLine(name, biblatex) {
    if (biblatex) return '\\addbibresource{' + name + '}';
    return '\\bibliography{' + String(name).replace(/\.bib$/i, '') + '}';
}

// ── Live session: pure helpers ──────────────────────────────────────────────

// "S. 12" / "PDF-S. 3" — the printed page when known, else the PDF page,
// never one made up.
function laciteHitPage(hit) {
    if (!hit) return '';
    if (hit.page_label) return hit.page_label;
    if (hit.pdf_page) return 'PDF-S. ' + hit.pdf_page;
    return hit.year ? String(hit.year) : '';
}

function laciteHits(n) {
    const value = Number(n) || 0;
    return value === 0 ? 'keine Treffer' : value + ' Treffer';
}

// One call as a plain sentence: parts of kind plain | quote | cite | muted | tag | mono.
function laciteCallParts(entry) {
    const e = entry || {};
    const args = e.args || {};
    const hit = (e.hits && e.hits[0]) || {};
    const done = !e.error;
    const quote = (q) => ({ kind: 'quote', text: '„' + (q || '') + '“' });
    switch (e.tool) {
    case 'find_passages':
        return [{ kind: 'plain', text: 'Sucht Textstellen zu ' }, quote(args.query),
            ...(done ? [{ kind: 'muted', text: ' → ' + laciteHits(e.count) }] : [])];
    case 'search_library':
        return [{ kind: 'plain', text: 'Sucht in der Bibliothek nach ' }, quote(args.query),
            ...(done ? [{ kind: 'muted', text: ' → ' + laciteHits(e.count) }] : [])];
    case 'read_passages':
        return [{ kind: 'plain', text: 'Liest um eine Textstelle in ' }, { kind: 'cite', text: args.citekey || hit.citekey || '' },
            ...(done && e.count ? [{ kind: 'muted', text: ' → ' + e.count + (e.count === 1 ? ' Abschnitt' : ' Abschnitte') }] : [])];
    case 'get_reference': {
        const about = [hit.title, hit.year].filter(Boolean).join(', ');
        return [{ kind: 'plain', text: 'Schlägt ' }, { kind: 'cite', text: args.citekey || hit.citekey || '' },
            { kind: 'plain', text: ' nach' }, ...(done && about ? [{ kind: 'muted', text: ' → ' + about }] : [])];
    }
    case 'add_by_doi':
        if (done && hit.citekey && !hit.created) {
            return [{ kind: 'plain', text: 'Findet über DOI ' }, { kind: 'cite', text: hit.citekey },
                { kind: 'muted', text: ' → schon in der Bibliothek' }];
        }
        if (done && hit.citekey) {
            return [{ kind: 'plain', text: 'Importiert über DOI ' }, { kind: 'tag', text: 'neu' }, { kind: 'cite', text: hit.citekey }];
        }
        return [{ kind: 'plain', text: 'Importiert über DOI ' }, { kind: 'mono', text: args.doi || '' }];
    case 'submit_report':
        return [{ kind: 'tag', text: 'Bericht' },
            { kind: 'plain', text: done ? 'Schlussbericht abgegeben' : 'Gibt einen Schlussbericht ab' }];
    case 'sync_bib':
        return [{ kind: 'plain', text: 'Aktualisiert die .bib-Dateien' },
            ...(done && e.count !== null && e.count !== undefined
                ? [{ kind: 'muted', text: ' → ' + e.count + (e.count === 1 ? ' Verbindung' : ' Verbindungen') }] : [])];
    default:
        return [{ kind: 'plain', text: 'Ruft ' }, { kind: 'mono', text: e.tool || '?' }, { kind: 'plain', text: ' auf' }];
    }
}

// Tools whose hits are shown as cards (passages and works one can open).
const LACITE_HIT_TOOLS = ['find_passages', 'read_passages', 'search_library'];

function laciteTime(iso) {
    const then = Date.parse(iso || '');
    if (Number.isNaN(then)) return '';
    return new Date(then).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function laciteClock(iso) {
    const then = Date.parse(iso || '');
    if (Number.isNaN(then)) return '';
    return new Date(then).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}

// The passage text for the core's `find`: without the excerpt's "…".
function laciteFindText(text) {
    return String(text || '').replace(/\s*…\s*$/, '').trim();
}

// ── Schlussberichte: pure helpers ───────────────────────────────────────────

// "heute, 14:32" / "22. Okt., 10:05" (now injectable for tests).
function laciteWhen(iso, now) {
    const then = Date.parse(iso || '');
    if (Number.isNaN(then)) return '';
    const d = new Date(then);
    const today = new Date(now === undefined ? Date.now() : now);
    const time = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === today.toDateString()) return 'heute, ' + time;
    const opts = { day: 'numeric', month: 'short' };
    if (d.getFullYear() !== today.getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString('de-DE', opts) + ', ' + time;
}

const LACITE_STATUS = {
    supported: { label: 'Belegt', cls: 'is-ok' },
    check: { label: 'Prüfen', cls: 'is-warn' },
    unsupported: { label: 'Ohne Beleg', cls: 'is-none' },
};

function laciteReportStatus(status) {
    return LACITE_STATUS[status] || LACITE_STATUS.unsupported;
}

// What a source card says about the quote, and whether it opens the PDF.
function laciteSourceTag(src) {
    const s = src || {};
    switch (s.state) {
    case 'verified': return { text: '✓ wörtlich im Text', cls: 'is-ok', open: true };
    case 'not_found': return { text: 'Zitat nicht im Text gefunden', cls: 'is-warn', open: true };
    case 'no_text': return { text: 'kein Volltext – nicht geprüft', cls: 'is-warn', open: true };
    case 'unknown_key': return { text: 'nicht in der Bibliothek', cls: 'is-mute', open: false };
    default: return { text: 'ohne Zitat – nicht geprüft', cls: 'is-warn', open: true };
    }
}

// The page a reader sees: the checked one, else the agent's hint as a PDF page.
function laciteSourcePage(src) {
    const s = src || {};
    if (s.page_label) return s.page_label;
    if (s.pdf_page_hint) return 'PDF-S. ' + s.pdf_page_hint;
    return '';
}

// The warning under a source whose quote could not be confirmed.
function laciteSourceWarning(src) {
    const s = src || {};
    const key = s.citekey || '';
    if (s.state === 'not_found') {
        const short = s.reason === 'too_short' ? ' Das Zitat ist zu kurz, um es sicher zu finden.' : '';
        const where = s.pdf_page_hint
            ? ' Die PDF öffnet auf PDF-Seite ' + s.pdf_page_hint + ', die der Agent angegeben hat.'
            : '';
        return 'Diesen Wortlaut gibt es in ' + key + ' nicht.' + short + where + ' Eine gedruckte Seite ist nicht bekannt.';
    }
    if (s.state === 'no_text') return 'Zu ' + key + ' gibt es keinen Volltext; das Zitat ließ sich nicht prüfen.';
    if (s.state === 'unknown_key') return key + ' gibt es nicht in der Bibliothek.';
    if (s.state === 'no_quote') return 'Der Agent hat kein Zitat angegeben; die Stelle ist nicht geprüft.';
    return '';
}

// "Claude Code · heute, 14:32 · Verbindung Dissertation · aus 23 Nachschlagungen"
function laciteReportMeta(report, now) {
    const r = report || {};
    const parts = [];
    if (r.client_name) parts.push(r.client_name);
    const when = laciteWhen(r.created_at, now);
    if (when) parts.push(when);
    if (r.project) parts.push('Verbindung ' + r.project);
    if (r.lookups) parts.push('aus ' + r.lookups + (r.lookups === 1 ? ' Nachschlagung' : ' Nachschlagungen'));
    return parts.join(' · ');
}

// Search hits grouped by report, in the order they came (newest report first).
function laciteGroupHits(hits) {
    const groups = [];
    for (const h of hits || []) {
        let g = groups.find((x) => x.id === h.report_id);
        if (!g) {
            g = { id: h.report_id, title: h.title, created_at: h.created_at, claims: [] };
            groups.push(g);
        }
        g.claims.push(h.claim);
    }
    return groups;
}

// "3 Aussagen in 2 Berichten"
function laciteHitsLine(hits) {
    const n = (hits || []).length;
    const reports = laciteGroupHits(hits).length;
    if (!n) return 'keine Aussage gefunden';
    return (n === 1 ? '1 Aussage' : n + ' Aussagen') + ' in ' + (reports === 1 ? '1 Bericht' : reports + ' Berichten');
}

// The claim text with the query's words marked: parts {text, mark}.
function laciteMarkParts(text, query) {
    const value = String(text || '');
    const words = String(query || '').trim().split(/\s+/).filter((w) => w.length > 1);
    if (!words.length) return [{ text: value, mark: false }];
    const esc = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const re = new RegExp('(' + esc.join('|') + ')', 'gi');
    const out = [];
    let last = 0;
    value.replace(re, (m, _g, at) => {
        if (at > last) out.push({ text: value.slice(last, at), mark: false });
        out.push({ text: m, mark: true });
        last = at + m.length;
        return m;
    });
    if (last < value.length) out.push({ text: value.slice(last), mark: false });
    return out;
}

const LACITE_LIVE_STORE = 'lacite.live';

function laciteLoadLive() {
    try {
        const raw = window.sessionStorage.getItem(LACITE_LIVE_STORE);
        const data = raw ? JSON.parse(raw) : null;
        return data && typeof data === 'object' ? data : null;
    } catch (e) {
        return null;
    }
}

function laciteSaveLive(state) {
    try {
        window.sessionStorage.setItem(LACITE_LIVE_STORE, JSON.stringify(state));
    } catch (e) { /* private window, blocked storage: the view works without */ }
}

const LACITE_EXAMPLE_PROMPT = 'Finde in meiner Bibliothek eine Textstelle, die belegt, dass PEM-Elektrolyse bei 80 °C effizienter arbeitet, und zitiere sie mit Seite.';

const LACITE_TABS = [
    { id: 'claude', name: 'VS Code · Claude Code' },
    { id: 'opencode', name: 'LMMs-Lab Writer · OpenCode' },
    { id: 'other', name: 'Anderer Assistent' },
];

async function laciteCopy(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (e) {
        return false;
    }
}

// ── The copy button (one place for "Kopiert ✓") ─────────────────────────────

const LaCiteCopy = {
    props: { text: { type: String, required: true }, label: { type: String, default: 'Kopieren' } },
    data() { return { copied: false }; },
    watch: { text() { this.copied = false; } },
    methods: {
        async copy() {
            await laciteCopy(this.text);
            this.copied = true;
        },
    },
    template: '<button type="button" class="lb-key" @click="copy">{{ copied ? \'Kopiert ✓\' : label }}</button>',
};

// ── The tutorial (also the empty state) ────────────────────────────────────

const LaCiteTutorial = {
    components: { LaCiteCopy },
    props: { hasConnections: Boolean },
    emits: ['connect', 'close'],
    data() {
        const snippet = '\\bibliography{literatur}';
        // Interpolated, not template text: Vue condenses the newline in text.
        return { snippet, demo: snippet + '\n… wie \\cite{Smith2020} zeigt …' };
    },
    template: `
<div class="lacite-tutorial">
  <section aria-labelledby="lacite-howto" class="lacite-section">
    <h2 id="lacite-howto" class="lacite-label">So funktioniert es · in drei Schritten</h2>
    <div class="lacite-steps">
      <article class="lb-set-card lacite-step">
        <div class="lacite-step-n">1</div>
        <h3 class="lacite-h3">Ordner verbinden</h3>
        <p class="lacite-p">Wähle den Ordner deines LaTeX-Projekts. LaCite legt dort <b>literatur.bib</b> ab und hält sie aktuell, sobald sich deine Bibliothek ändert.</p>
        <div class="lacite-folder-demo">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M3 7h6l2 2h10v10H3z"/></svg>
          <div class="lacite-folder-demo-lines">
            <span class="lacite-muted">Dissertation\\</span>
            <span class="lb-mono">main.tex</span>
            <span class="lb-mono lacite-accent">literatur.bib ← LocalBib</span>
          </div>
        </div>
      </article>
      <article class="lb-set-card lacite-step">
        <div class="lacite-step-n">2</div>
        <h3 class="lacite-h3">In LaTeX zitieren</h3>
        <p class="lacite-p">Binde die Datei einmal ein und zitiere mit dem Schlüssel aus LocalBib. Dein Editor schlägt die Schlüssel beim Tippen vor.</p>
        <div class="lacite-step-foot">
          <div class="lacite-code">{{ demo }}</div>
          <la-cite-copy :text="snippet" label="Zeilen kopieren" class="lacite-self-start"></la-cite-copy>
        </div>
      </article>
      <article class="lb-set-card lacite-step">
        <div class="lacite-step-head"><div class="lacite-step-n">3</div><span class="lacite-tag">optional</span></div>
        <h3 class="lacite-h3">KI-Assistent verbinden</h3>
        <p class="lacite-p">Claude Code oder OpenCode durchsuchen deine Bibliothek, lesen die passende Stelle nach und zitieren mit Seite.</p>
        <div class="lacite-step-foot">
          <div class="lacite-bubble lacite-bubble-me">Belege, dass Elektrolyse bei 80 °C effizienter ist.</div>
          <div class="lacite-bubble lacite-bubble-ai">Gefunden in Niu 2026, S. 12 → <span class="lb-mono lacite-accent-ink">\\cite[S.~12]{Niu.2026}</span></div>
        </div>
      </article>
    </div>
  </section>
  <div class="lacite-cta">
    <button type="button" class="lb-key lb-key-primary lacite-key-lg" @click="$emit('connect')">{{ hasConnections ? 'Neues Projekt verbinden' : 'Erstes Projekt verbinden' }}</button>
    <button v-if="hasConnections" type="button" class="lb-key" @click="$emit('close')">Zu deinen Verbindungen</button>
    <span class="lacite-muted lacite-small">Funktioniert mit VS Code + LaTeX Workshop, LMMs-Lab Writer, TeXstudio – mit jedem Editor, der .bib-Dateien liest.</span>
  </div>
  <aside class="lacite-note">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>
    <p>LaCite schreibt nur die eine Datei, die du festlegst, und löscht nie etwas. Trennst du eine Verbindung, bleibt die Datei im Ordner liegen. Deine Schlüssel ändern sich nur, wenn du sie in LocalBib selbst umbenennst.</p>
  </aside>
</div>`,
};

// ── The dialog "Neue Verbindung" (3 steps) ──────────────────────────────────

const LaCiteDialog = {
    components: { LaCiteCopy },
    props: {
        // 'new' | 'edit' | 'source' ("Neue Quelle wählen" starts at step 2)
        mode: { type: String, default: 'new' },
        conn: { type: Object, default: null },
    },
    emits: ['close', 'saved', 'assistant'],
    data() {
        const c = this.conn || {};
        return {
            step: this.mode === 'source' ? 2 : 1,
            folder: c.folder || '',
            name: c.name || '',
            nameTouched: !!c.name,
            check: null,
            checking: false,
            picking: false,
            pasteOpen: false,
            pasteValue: c.folder || '',
            error: '',
            sources: null,
            sourceKind: (c.source && c.source.kind) || 'library',
            collectionId: c.source && c.source.kind === 'collection' && this.mode !== 'source' ? c.source.id : null,
            filename: c.filename || 'literatur.bib',
            saving: false,
            confirmForeign: '',
            result: null,
        };
    },
    computed: {
        title() {
            if (this.mode === 'edit') return 'Verbindung bearbeiten';
            if (this.mode === 'source') return 'Neue Quelle wählen';
            return 'Neue Verbindung';
        },
        steps() {
            return ['Ordner', 'Quelle', 'Fertig'].map((name, i) => ({
                n: i + 1, name, on: i + 1 <= this.step, current: i + 1 === this.step,
            }));
        },
        folderOk() { return !!(this.check && this.check.ok); },
        texLine() {
            const doc = this.check && this.check.document;
            if (!doc || !doc.tex_files) return 'Ordner gefunden · noch keine .tex-Datei darin';
            return 'Ordner gefunden · enthält ' + doc.main_tex;
        },
        canNext() {
            if (this.step === 1) return this.folderOk && !this.checking;
            if (this.step === 2) return this.sourceKind === 'library' || this.collectionId !== null;
            return false;
        },
        nextLabel() {
            if (this.step === 2) return this.mode === 'new' ? 'Verbinden' : 'Speichern';
            return 'Weiter';
        },
        collections() { return (this.sources && this.sources.collections) || []; },
        libraryCount() { return this.sources ? laciteCount(this.sources.library.count) : ''; },
        resultConn() { return this.result && this.result.connection; },
        resultText() {
            const c = this.resultConn;
            if (!c) return '';
            const st = c.status || {};
            const count = laciteCount(st.entries || 0);
            if (c.source.kind === 'collection' && st.source) return count + ' aus „' + st.source.name + '“';
            return count;
        },
        resultWritten() {
            const st = (this.resultConn && this.resultConn.status) || {};
            return st.state === 'ok';
        },
        doc() { return this.resultConn && this.resultConn.document; },
    },
    mounted() {
        this.loadSources();
        if (this.folder) this.runCheck(this.folder);
        document.addEventListener('keydown', this.onKey);
    },
    unmounted() { document.removeEventListener('keydown', this.onKey); },
    methods: {
        onKey(e) { if (e.key === 'Escape') this.$emit('close'); },
        async loadSources() {
            try {
                this.sources = await lcApi('/sources');
            } catch (e) {
                this.sources = { library: { count: 0 }, collections: [], collections_supported: false };
            }
        },
        async pick() {
            this.error = '';
            this.picking = true;
            try {
                const out = await lcApi('/pick-folder', { method: 'POST', body: { initial: this.folder } });
                if (out && out.folder) await this.useFolder(out.folder);
            } catch (e) {
                this.error = laciteErrorText(e);
                if (e.code === 'error.lacite.picker_unavailable') this.pasteOpen = true;
            } finally {
                this.picking = false;
            }
        },
        async usePasted() {
            await this.useFolder(this.pasteValue.trim().replace(/^"(.*)"$/, '$1'));
        },
        async useFolder(folder) {
            this.folder = folder;
            this.pasteValue = folder;
            await this.runCheck(folder);
        },
        async runCheck(folder) {
            this.checking = true;
            this.error = '';
            try {
                this.check = await lcApi('/check-folder', { method: 'POST', body: { folder, filename: this.filename } });
                if (!this.check.ok) {
                    this.error = laciteErrorText({ code: this.check.error });
                } else if (!this.nameTouched) {
                    this.name = this.check.name;
                }
            } catch (e) {
                this.check = null;
                this.error = laciteErrorText(e);
            } finally {
                this.checking = false;
            }
        },
        chooseLibrary() { this.sourceKind = 'library'; },
        chooseCollection() {
            this.sourceKind = 'collection';
            if (this.collectionId === null && this.collections.length) this.collectionId = this.collections[0].id;
        },
        back() {
            this.error = '';
            this.confirmForeign = '';
            if (this.step > 1) this.step -= 1;
        },
        async next() {
            if (!this.canNext) return;
            if (this.step === 1) {
                this.step = 2;
                return;
            }
            await this.save(false);
        },
        async save(overwrite) {
            this.saving = true;
            this.error = '';
            const body = {
                name: this.name.trim() || null,
                folder: this.folder,
                filename: this.filename.trim() || 'literatur.bib',
                source: this.sourceKind === 'library' ? { kind: 'library' } : { kind: 'collection', id: this.collectionId },
                overwrite,
            };
            try {
                const out = this.mode === 'new'
                    ? await lcApi('/connections', { method: 'POST', body })
                    : await lcApi('/connections/' + encodeURIComponent(this.conn.id), { method: 'PUT', body });
                if (out.needs_confirm) {
                    this.confirmForeign = out.file;
                    return;
                }
                this.confirmForeign = '';
                this.result = out;
                this.step = 3;
                this.$emit('saved', out.connection);
            } catch (e) {
                this.error = laciteErrorText(e);
            } finally {
                this.saving = false;
            }
        },
    },
    template: `
<div class="lacite-overlay" @click.self="$emit('close')">
  <div class="lacite-dialog" role="dialog" aria-modal="true" aria-labelledby="lacite-dlg-title">
    <div class="lacite-dialog-head">
      <div class="lacite-row-between">
        <h2 id="lacite-dlg-title" class="lacite-dialog-title">{{ title }}</h2>
        <button type="button" class="lb-key lb-key-icon lacite-key-ghost" aria-label="Schließen" @click="$emit('close')">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
      </div>
      <ol class="lacite-progress" aria-label="Schritte">
        <li v-for="s in steps" :key="s.n" :class="{ 'is-on': s.on, 'is-current': s.current }" :aria-current="s.current ? 'step' : null">
          <div class="lacite-progress-bar"></div>
          <span>{{ s.n }} · {{ s.name }}</span>
        </li>
      </ol>
    </div>

    <div class="lacite-dialog-body">
      <div v-if="step === 1" class="lacite-stack">
        <div>
          <h3 class="lacite-h3">Wo liegt dein LaTeX-Projekt?</h3>
          <p class="lacite-p">Der Ordner, in dem deine <span class="lb-mono">.tex</span>-Datei liegt. LaCite legt die <span class="lb-mono">.bib</span>-Datei direkt daneben.</p>
        </div>
        <button v-if="!folderOk" type="button" class="lacite-choice lacite-pick" :disabled="picking" @click="pick">
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M3 7h6l2 2h10v10H3z"/><path d="M12 12v5M9.5 14.5h5"/></svg>
          <span class="lacite-strong">{{ picking ? 'Ordnerdialog ist offen …' : 'Ordner auswählen …' }}</span>
          <span class="lacite-muted lacite-small">öffnet den Ordnerdialog deines Systems</span>
        </button>
        <div v-else class="lacite-stack-sm">
          <div class="lacite-folder-row">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M3 7h6l2 2h10v10H3z"/></svg>
            <div class="lacite-grow">
              <div class="lb-mono lb-truncate lacite-path" :title="folder">{{ folder }}</div>
              <div class="lacite-small lacite-ok">✓ {{ texLine }}</div>
            </div>
            <button type="button" class="lb-key" :disabled="picking" @click="pick">Ändern</button>
          </div>
          <label class="lacite-field">
            <span class="lb-field-label">Name der Verbindung</span>
            <input class="lb-input" type="text" v-model="name" @input="nameTouched = true" maxlength="80">
          </label>
        </div>
        <div v-if="pasteOpen" class="lacite-paste">
          <label class="lacite-field lacite-grow">
            <span class="lb-field-label">Pfad des Ordners</span>
            <input class="lb-input lb-mono" type="text" v-model="pasteValue" placeholder="C:\\Users\\…\\Dissertation" @keydown.enter.prevent="usePasted">
          </label>
          <button type="button" class="lb-key" :disabled="!pasteValue.trim() || checking" @click="usePasted">Prüfen</button>
        </div>
        <p v-if="error" class="lacite-error" role="alert">{{ error }}</p>
        <p v-if="!pasteOpen" class="lacite-small lacite-muted">Dialog geht nicht auf? <button type="button" class="lacite-link" @click="pasteOpen = true">Pfad stattdessen einfügen</button></p>
      </div>

      <div v-else-if="step === 2" class="lacite-stack">
        <div>
          <h3 class="lacite-h3">Was soll in die Datei?</h3>
          <p class="lacite-p">LaTeX übernimmt ins Literaturverzeichnis nur, was du wirklich zitierst.</p>
        </div>
        <button type="button" class="lacite-choice" :class="{ 'is-on': sourceKind === 'library' }" :aria-pressed="sourceKind === 'library'" @click="chooseLibrary">
          <span class="lacite-dot" aria-hidden="true"></span>
          <span class="lacite-choice-text">
            <span class="lacite-strong">Ganze Bibliothek <span class="lacite-muted lacite-normal" v-if="libraryCount">· {{ libraryCount }}</span> <span class="lacite-tag lacite-tag-accent">empfohlen</span></span>
            <span class="lacite-small">Am einfachsten: Jeder Eintrag ist zitierbar, auch neue.</span>
          </span>
        </button>
        <button type="button" class="lacite-choice" :class="{ 'is-on': sourceKind === 'collection' }" :aria-pressed="sourceKind === 'collection'"
                :disabled="sources && !sources.collections_supported" @click="chooseCollection">
          <span class="lacite-dot" aria-hidden="true"></span>
          <span class="lacite-choice-text">
            <span class="lacite-strong">Eine Sammlung</span>
            <span class="lacite-small">Nur die Einträge einer Sammlung – gut, wenn du Ordnung pro Projekt willst.</span>
          </span>
        </button>
        <div v-if="sourceKind === 'collection'" class="lacite-list" role="listbox" aria-label="Sammlung">
          <p v-if="!collections.length" class="lacite-list-empty lacite-muted lacite-small">Du hast noch keine Sammlung. Lege sie in LocalBib an oder nimm die ganze Bibliothek.</p>
          <button v-for="c in collections" :key="c.id" type="button" role="option" class="lacite-list-row"
                  :class="{ 'is-on': c.id === collectionId }" :aria-selected="c.id === collectionId" @click="collectionId = c.id">
            <span>{{ c.name }}<span v-if="c.archived" class="lacite-muted"> · archiviert</span></span>
            <span class="lb-mono lacite-muted">{{ laciteCount(c.item_count) }}</span>
          </button>
        </div>
        <label class="lacite-field lacite-field-narrow">
          <span class="lb-field-label">Dateiname</span>
          <input class="lb-input lb-mono" type="text" v-model="filename">
        </label>
        <div v-if="confirmForeign" class="lacite-confirm" role="alert">
          <span>In diesem Ordner liegt schon <b>{{ filename }}</b>, die nicht von LaCite stammt. Soll LaCite sie überschreiben? Was darin steht, geht dabei verloren.</span>
          <span class="lacite-actions">
            <button type="button" class="lb-key" @click="confirmForeign = ''">Abbrechen</button>
            <button type="button" class="lb-key lacite-key-danger" :disabled="saving" @click="save(true)">Überschreiben</button>
          </span>
        </div>
        <p v-if="error" class="lacite-error" role="alert">{{ error }}</p>
      </div>

      <div v-else class="lacite-stack">
        <div v-if="resultWritten" class="lacite-callout is-ok">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>
          <div>
            <div class="lacite-strong">{{ resultConn.filename }} geschrieben · {{ resultText }}</div>
            <div class="lacite-small">LaCite hält sie ab jetzt automatisch aktuell.</div>
          </div>
        </div>
        <div v-else class="lacite-callout is-warn">
          <div>
            <div class="lacite-strong">Verbunden, aber noch nicht geschrieben</div>
            <div class="lacite-small">{{ laciteErrorText({ code: resultConn && resultConn.status.error }) }}</div>
          </div>
        </div>
        <div v-if="doc && doc.included_in" class="lacite-stack-sm">
          <h3 class="lacite-h3">Schon eingebunden</h3>
          <p class="lacite-p">In <span class="lb-mono">{{ doc.included_in }}</span> ist die Datei bereits eingebunden. Du zitierst mit <span class="lb-mono">\\cite{Schlüssel}</span>.</p>
        </div>
        <div v-else-if="doc" class="lacite-stack-sm">
          <h3 class="lacite-h3">Jetzt einmal einbinden</h3>
          <p class="lacite-p" v-if="doc.main_tex">In <span class="lb-mono">{{ doc.main_tex }}</span> nutzt du <b>{{ doc.biblatex ? 'biblatex' : 'BibTeX' }}</b> – deshalb {{ doc.biblatex ? 'diese Zeilen' : 'diese Zeile' }}:</p>
          <p class="lacite-p" v-else>Füge in deine Hauptdatei {{ doc.biblatex ? 'diese Zeilen' : 'diese Zeile' }} ein:</p>
          <div class="lacite-code">{{ doc.snippet }}</div>
          <div class="lacite-row">
            <la-cite-copy :text="doc.snippet" :label="doc.biblatex ? 'Zeilen kopieren' : 'Zeile kopieren'"></la-cite-copy>
            <span class="lacite-small lacite-muted">Dann zitierst du mit <span class="lb-mono">\\cite{Smith2020}</span>.</span>
          </div>
        </div>
      </div>
    </div>

    <div class="lacite-dialog-foot">
      <button v-if="step === 2 && mode !== 'source'" type="button" class="lb-key lacite-key-ghost" @click="back">← Zurück</button>
      <button v-else-if="step !== 3" type="button" class="lb-key lacite-key-ghost" @click="$emit('close')">Abbrechen</button>
      <span v-else></span>
      <div class="lacite-actions">
        <template v-if="step === 3">
          <button type="button" class="lb-key" @click="$emit('assistant')">KI-Assistent verbinden</button>
          <button type="button" class="lb-key lb-key-primary" @click="$emit('close')">Fertig</button>
        </template>
        <button v-else type="button" class="lb-key lb-key-primary" :disabled="!canNext || saving" @click="next">{{ saving ? 'Einen Moment …' : nextLabel }}</button>
      </div>
    </div>
  </div>
</div>`,
};
LaCiteDialog.methods.laciteCount = laciteCount;
LaCiteDialog.methods.laciteErrorText = laciteErrorText;

// ── The missing keys of one card (GET connections/{id}/missing) ─────────────

const LACITE_MISSING_ROWS = 4;

const LaCiteMissing = {
    components: { LaCiteCopy },
    props: { conn: { type: Object, required: true }, data: { type: Object, required: true } },
    data() { return { allFound: false }; },
    computed: {
        groups() { return laciteMissingGroups(this.data); },
        shownFound() { return this.allFound ? this.groups.found : this.groups.found.slice(0, LACITE_MISSING_ROWS); },
        importCount() { return this.groups.found.length + this.groups.library.length; },
        // A core before contract 2.6 has no handover: the button stays away.
        canImport() { return typeof LB.importBibtex === 'function' && !!(this.data.bibtex || '').trim(); },
        collectionName() {
            const src = (this.conn.status && this.conn.status.source) || {};
            return src.kind === 'collection' ? src.name || '' : '';
        },
        biblatex() { return !!(this.conn.document && this.conn.document.biblatex); },
        mainTex() { return (this.conn.document && this.conn.document.main_tex) || 'der Hauptdatei'; },
        stale() { return this.data.named_bib_files || []; },
    },
    methods: {
        typeLabel(k) { return k.entry_type ? '@' + k.entry_type : ''; },
        line(name) { return laciteIncludeLine(name, this.biblatex); },
        stem(name) { return String(name).replace(/\.bib$/i, ''); },
        runImport() {
            LB.importBibtex(this.data.bibtex, {
                collection: this.data.collection_id, filename: laciteImportName(this.groups, this.conn),
            });
        },
        pickOther() {
            const query = { bibtex: '1' };
            if (this.data.collection_id !== null && this.data.collection_id !== undefined) query.collection = String(this.data.collection_id);
            this.$router.push({ path: '/import', query });
        },
    },
    template: `
<section aria-label="Fehlende Schlüssel" class="lacite-missing" data-testid="lacite-missing">
  <div v-if="groups.found.length" class="lacite-grp" data-group="found">
    <div class="lacite-row-between lacite-align-baseline">
      <h4 class="lacite-h4">In alter .bib gefunden <span class="lacite-count">{{ groups.found.length }}</span></h4>
      <span class="lb-mono lacite-file">{{ laciteFilesLine(groups.files) }}</span>
    </div>
    <div>
      <div v-for="k in shownFound" :key="k.key" class="lacite-krow">
        <span class="lb-mono lb-truncate" :title="k.key">{{ k.key }}</span>
        <span class="lb-truncate lacite-ink2">{{ k.title }}<span v-if="typeLabel(k)" class="lacite-muted"> · {{ typeLabel(k) }}</span></span>
        <span class="lb-mono lacite-file">{{ k.file }}</span>
      </div>
      <button v-if="groups.found.length > shownFound.length" type="button" class="lacite-textaction lacite-flush" @click="allFound = true">Alle {{ groups.found.length }} zeigen</button>
    </div>
  </div>

  <div v-if="groups.library.length" class="lacite-grp" data-group="library">
    <h4 class="lacite-h4">In der Bibliothek, nicht in der Sammlung <span class="lacite-count">{{ groups.library.length }}</span></h4>
    <div>
      <div v-for="k in groups.library" :key="k.key" class="lacite-krow">
        <span class="lb-mono lb-truncate" :title="k.key">{{ k.key }}</span>
        <span class="lb-truncate lacite-ink2">{{ k.title }}</span>
        <span></span>
      </div>
    </div>
    <p class="lacite-p lacite-small lacite-muted">Kommen beim Import mit in die Sammlung<template v-if="collectionName"> „{{ collectionName }}“</template>. Doppelt angelegt wird nichts.</p>
  </div>

  <div v-if="importCount && canImport" class="lacite-row lacite-wrap lacite-import">
    <button type="button" class="lb-key lb-key-primary" data-testid="lacite-import" @click="runImport">In LocalBib importieren ({{ importCount }})</button>
    <span class="lacite-small lacite-muted">Du siehst vorher die Vorschau des BibTeX-Imports. Die Schlüssel bleiben genau so.</span>
  </div>

  <div v-if="groups.notFound.length" class="lacite-grp" data-group="not_found">
    <h4 class="lacite-h4">Nirgends gefunden <span class="lacite-count">{{ groups.notFound.length }}</span></h4>
    <div>
      <div v-for="k in groups.notFound" :key="k.key" class="lacite-krow">
        <span class="lb-mono lb-truncate" :title="k.key">{{ k.key }}</span>
        <span class="lacite-muted"><template v-if="k.suggestion">Tippfehler? In der Bibliothek gibt es <span class="lb-mono lacite-ink2">{{ k.suggestion }}</span>.</template></span>
        <span></span>
      </div>
    </div>
    <div class="lacite-row lacite-wrap">
      <button type="button" class="lb-key" data-testid="lacite-pick-bib" @click="pickOther">Andere .bib-Datei wählen …</button>
      <span class="lacite-small lacite-muted">oder im LaTeX-Dokument korrigieren</span>
    </div>
  </div>

  <div v-for="name in stale" :key="name" class="lacite-stale" data-testid="lacite-stale">
    <p class="lacite-p"><b>{{ name }} ist noch eingebunden.</b> Nach dem Import stünden die Schlüssel doppelt da.
      <template v-if="biblatex">Nimm diese Zeile aus {{ mainTex }} heraus:</template>
      <template v-else>Nimm {{ stem(name) }} aus der Zeile \\bibliography{…} in {{ mainTex }} heraus:</template></p>
    <div class="lacite-row">
      <div class="lacite-code lacite-code-inline">{{ line(name) }}</div>
      <la-cite-copy :text="line(name)"></la-cite-copy>
    </div>
  </div>
</section>`,
};
LaCiteMissing.methods.laciteFilesLine = laciteFilesLine;

// ── One connection card ─────────────────────────────────────────────────────

const LaCiteCard = {
    components: { LaCiteCopy, LaCiteMissing },
    props: { conn: { type: Object, required: true }, now: { type: Number, default: 0 } },
    emits: ['edit', 'source', 'changed', 'removed', 'toast'],
    data() {
        return {
            confirming: false, confirmOverwrite: false, syncing: false, error: '',
            missingOpen: false, missingData: null, missingLoading: false, missingError: '',
        };
    },
    watch: {
        // The list follows the card: a sync or an import changes which keys miss.
        missingSignature() { if (this.missingOpen) this.loadMissing(); },
    },
    computed: {
        st() { return this.conn.status || {}; },
        pill() { return this.syncing ? { label: 'Wird aktualisiert …', tone: 'soft' } : laciteStatus(this.conn); },
        sourceLabel() { return laciteSourceLabel(this.conn); },
        paused() { return this.st.state === 'paused'; },
        foreign() { return this.st.state === 'foreign'; },
        failed() { return this.st.state === 'error'; },
        sourceGone() { return this.paused && this.st.source && this.st.source.deleted; },
        collectionName() { return (this.st.source && this.st.source.name) || ''; },
        doc() { return this.conn.document; },
        updated() { return laciteAgo(this.st.last_written || this.st.last_sync, this.now || undefined); },
        missing() { return laciteMissingText(this.conn); },
        missingSignature() { return ((this.doc && this.doc.missing_keys) || []).join('\n'); },
        errorText() { return laciteErrorText({ code: this.st.error }); },
    },
    methods: {
        toggleMissing() {
            this.missingOpen = !this.missingOpen;
            if (this.missingOpen) this.loadMissing();
        },
        async loadMissing() {
            const ticket = (this._missingTicket = (this._missingTicket || 0) + 1);
            this.missingLoading = true;
            this.missingError = '';
            try {
                const data = await lcApi('/connections/' + encodeURIComponent(this.conn.id) + '/missing');
                if (ticket === this._missingTicket) this.missingData = data;
            } catch (e) {
                if (ticket === this._missingTicket) this.missingError = laciteErrorText(e);
            } finally {
                if (ticket === this._missingTicket) this.missingLoading = false;
            }
        },
        async sync(overwrite) {
            this.confirmOverwrite = false;
            this.syncing = true;
            this.error = '';
            try {
                const out = await lcApi('/connections/' + encodeURIComponent(this.conn.id) + '/sync', {
                    method: 'POST', body: { overwrite: !!overwrite } });
                this.$emit('changed', out.connection);
            } catch (e) {
                this.error = laciteErrorText(e);
            } finally {
                this.syncing = false;
            }
        },
        async openFolder() {
            this.error = '';
            try {
                await lcApi('/connections/' + encodeURIComponent(this.conn.id) + '/open-folder', { method: 'POST' });
            } catch (e) {
                this.error = laciteErrorText(e);
            }
        },
        async remove() {
            this.error = '';
            try {
                await lcApi('/connections/' + encodeURIComponent(this.conn.id), { method: 'DELETE' });
                this.$emit('removed', this.conn);
            } catch (e) {
                this.error = laciteErrorText(e);
            }
        },
    },
    template: `
<article class="lb-set-card lacite-card" :class="{ 'is-alert': paused || failed || foreign }" :data-connection="conn.id">
  <div class="lacite-row-between">
    <h3 class="lacite-h3">{{ conn.name }}</h3>
    <span class="lacite-pill" :class="'is-' + pill.tone"><span class="lacite-pill-dot"></span>{{ pill.label }}</span>
  </div>
  <div class="lacite-facts">
    <span class="lacite-muted">Ordner</span>
    <span class="lb-mono lb-truncate" :title="conn.folder + ' → ' + conn.filename">{{ conn.folder }}  →  {{ conn.filename }}</span>
    <span class="lacite-muted">Quelle</span>
    <span :class="{ 'lacite-struck': sourceGone }">{{ sourceLabel }}</span>
    <template v-if="doc && !paused && !foreign">
      <span class="lacite-muted">Im Dokument</span>
      <span v-if="doc.included_in" class="lacite-ok">✓ Eingebunden in {{ doc.included_in }}<template v-if="updated"> · aktualisiert {{ updated }}</template></span>
      <span v-else-if="doc.tex_files" class="lacite-warn">Noch nicht eingebunden – in {{ doc.main_tex }} fehlt die Zeile:</span>
      <span v-else class="lacite-warn">Noch keine .tex-Datei im Ordner – binde die Datei so ein:</span>
    </template>
  </div>
  <div v-if="doc && !doc.included_in && !paused && !foreign" class="lacite-row lacite-wrap">
    <div class="lacite-code lacite-code-line">{{ doc.snippet_line }}</div>
    <la-cite-copy :text="doc.snippet_line"></la-cite-copy>
  </div>
  <div v-if="missing && !paused" class="lacite-callout is-warn lacite-small-callout lacite-missing-callout">
    <span class="lacite-missing-text">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/></svg>
      <span>{{ missing.head }}<template v-for="(k, i) in missing.keys" :key="k"><span class="lb-mono">{{ k }}</span><template v-if="i < missing.keys.length - 1">, </template></template><template v-if="missing.rest"> und {{ missing.rest }} weitere</template>.</span>
    </span>
    <button type="button" class="lacite-textaction lacite-on-warn" data-testid="lacite-missing-toggle"
            :aria-expanded="missingOpen ? 'true' : 'false'" @click="toggleMissing">{{ missingOpen ? 'Ausblenden' : 'Fehlende ansehen …' }}</button>
  </div>
  <template v-if="missing && !paused && missingOpen">
    <p v-if="missingLoading && !missingData" class="lacite-small lacite-muted">Sucht in den .bib-Dateien des Projekts …</p>
    <p v-else-if="missingError" class="lacite-error" role="alert">{{ missingError }}</p>
    <la-cite-missing v-else-if="missingData" :conn="conn" :data="missingData"></la-cite-missing>
  </template>
  <p v-if="sourceGone" class="lacite-p">Die Sammlung „{{ collectionName }}“ wurde gelöscht. Die Datei bleibt so, wie sie war, wird aber nicht mehr aktualisiert. Wähle eine neue Quelle, damit es weitergeht.</p>
  <p v-else-if="paused" class="lacite-p">Die Sammlung gibt es nicht mehr. Die Datei bleibt so, wie sie war, wird aber nicht mehr aktualisiert. Wähle eine neue Quelle, damit es weitergeht.</p>
  <p v-if="foreign" class="lacite-p">In <span class="lb-mono">{{ conn.filename }}</span> fehlt die LaCite-Kopfzeile – die Datei wurde von Hand geändert oder stammt nicht von LaCite. LaCite überschreibt sie erst, wenn du zustimmst.</p>
  <p v-if="failed" class="lacite-error">{{ errorText }}</p>
  <p v-if="error" class="lacite-error" role="alert">{{ error }}</p>
  <div v-if="confirmOverwrite && foreign" class="lacite-confirm" role="alert">
    <span>Soll LaCite <b>{{ conn.filename }}</b> überschreiben? Was darin steht, geht dabei verloren.</span>
    <span class="lacite-actions">
      <button type="button" class="lb-key" @click="confirmOverwrite = false">Abbrechen</button>
      <button type="button" class="lb-key lacite-key-danger" :disabled="syncing" @click="sync(true)">Überschreiben</button>
    </span>
  </div>
  <div v-if="confirming" class="lacite-confirm">
    <span>Verbindung trennen? <b>{{ conn.filename }} bleibt im Ordner</b>, wird aber nicht mehr aktualisiert.</span>
    <span class="lacite-actions">
      <button type="button" class="lb-key" @click="confirming = false">Abbrechen</button>
      <button type="button" class="lb-key lacite-key-danger" @click="remove">Trennen</button>
    </span>
  </div>
  <div class="lacite-actions lacite-wrap">
    <template v-if="paused">
      <button type="button" class="lb-key lb-key-primary" @click="$emit('source', conn)">Neue Quelle wählen</button>
      <button type="button" class="lb-key" @click="openFolder">Ordner öffnen</button>
    </template>
    <template v-else-if="foreign">
      <button type="button" class="lb-key lb-key-primary" :disabled="syncing || confirmOverwrite" @click="confirmOverwrite = true">Überschreiben …</button>
      <button type="button" class="lb-key" @click="openFolder">Ordner öffnen</button>
      <button type="button" class="lb-key" @click="$emit('edit', conn)">Bearbeiten</button>
    </template>
    <template v-else>
      <button type="button" class="lb-key" @click="openFolder">Ordner öffnen</button>
      <button type="button" class="lb-key" :disabled="syncing" @click="sync(false)">{{ syncing ? 'Läuft …' : 'Jetzt aktualisieren' }}</button>
      <button type="button" class="lb-key" @click="$emit('edit', conn)">Bearbeiten</button>
    </template>
    <button type="button" class="lb-key lacite-key-danger" @click="confirming = true">Trennen</button>
  </div>
</article>`,
};

// ── "KI-Assistent verbinden" ───────────────────────────────────────────────

const LaCiteAssistant = {
    components: { LaCiteCopy },
    props: { mcp: { type: Object, default: null }, now: { type: Number, default: 0 } },
    data() { return { tab: 'claude', tabs: LACITE_TABS, prompt: LACITE_EXAMPLE_PROMPT }; },
    computed: {
        origin() { return window.location.origin; },
        snippet() { return laciteAssistantSnippet(this.tab, this.origin); },
        lastSeen() {
            if (!this.mcp) return null;
            return 'Zuletzt verbunden: ' + this.mcp.name + ' · ' + laciteAgo(this.mcp.at, this.now || undefined);
        },
    },
    template: `
<section id="lacite-ki" aria-labelledby="lacite-ki-h" class="lacite-section">
  <div class="lacite-row-between lacite-wrap lacite-align-end">
    <div class="lacite-stack-xs">
      <h2 id="lacite-ki-h" class="lacite-label">KI-Assistent verbinden</h2>
      <p class="lacite-p lacite-measure">Einmal einrichten – danach kann dein Assistent im Editor deine Bibliothek durchsuchen, die passende Textstelle lesen und mit Seite zitieren.</p>
    </div>
    <span v-if="lastSeen" class="lacite-pill is-ok"><span class="lacite-pill-dot"></span>{{ lastSeen }}</span>
    <span v-else class="lacite-pill is-soft"><span class="lacite-pill-dot"></span>Noch kein Assistent verbunden</span>
  </div>
  <div class="lb-set-card lacite-ki-card">
    <div class="lacite-ki-tabs">
      <div class="lb-tabs" role="tablist" aria-label="Editor">
        <button v-for="t in tabs" :key="t.id" type="button" role="tab" class="lb-tab" :class="{ 'is-active': tab === t.id }"
                :aria-selected="tab === t.id" @click="tab = t.id">{{ t.name }}</button>
      </div>
    </div>
    <div class="lacite-ki-body">
      <ol v-if="tab === 'claude'" class="lacite-ol">
        <li>Öffne in VS Code das Terminal (<span class="lb-mono">Strg+ö</span>) und füge diesen Befehl ein:
          <div class="lacite-row lacite-wrap lacite-mt"><div class="lacite-code lacite-code-line lacite-grow">{{ snippet }}</div><la-cite-copy :text="snippet"></la-cite-copy></div>
        </li>
        <li>Starte Claude Code neu. Fertig – oben erscheint „Zuletzt verbunden“.</li>
      </ol>
      <ol v-else-if="tab === 'opencode'" class="lacite-ol">
        <li>Öffne in deinem Projektordner die Datei <span class="lb-mono">opencode.json</span> (oder lege sie an) und füge ein:
          <div class="lacite-row lacite-wrap lacite-mt lacite-align-start"><div class="lacite-code lacite-grow">{{ snippet }}</div><la-cite-copy :text="snippet"></la-cite-copy></div>
        </li>
        <li>Öffne das Projekt in LMMs-Lab Writer neu.</li>
      </ol>
      <div v-else class="lacite-stack-sm">
        <p class="lacite-p">Jeder Assistent, der MCP über HTTP spricht, verbindet sich mit dieser Adresse:</p>
        <div class="lacite-row lacite-wrap"><div class="lacite-code lacite-code-line lacite-grow">{{ snippet }}</div><la-cite-copy :text="snippet"></la-cite-copy></div>
      </div>
      <p class="lacite-small lacite-muted">Die Verbindung funktioniert, solange LocalBib läuft. Sie ist nur auf diesem Rechner erreichbar.</p>
    </div>
  </div>
  <div class="lacite-two">
    <div class="lb-set-card lacite-card">
      <h3 class="lacite-h3 lacite-h3-sm">Das kann dein Assistent jetzt</h3>
      <ul class="lacite-ul">
        <li>Quellen in deiner Bibliothek suchen</li>
        <li>Textstellen finden – mit Seitenzahl</li>
        <li>rund um eine Stelle weiterlesen</li>
        <li>einen Eintrag per DOI hinzufügen</li>
        <li>die .bib-Datei sofort aktualisieren</li>
      </ul>
      <p class="lacite-small lacite-muted">Seitenzahlen: LaCite nennt die gedruckte Seite, wenn sie sicher bestimmbar ist – sonst ausdrücklich die PDF-Seite („PDF-S. 3“). Erfunden wird keine.</p>
    </div>
    <div class="lb-set-card lacite-card">
      <h3 class="lacite-h3 lacite-h3-sm">Zum Ausprobieren</h3>
      <div class="lacite-prompt">„{{ prompt }}“</div>
      <la-cite-copy :text="prompt" label="Prompt kopieren" class="lacite-self-start"></la-cite-copy>
    </div>
  </div>
</section>`,
};

// ── The live session (/lacite/live) ─────────────────────────────────────────
//
// What the writing agent looked up over MCP, newest on top, live over SSE
// (GET /events; GET /session on opening and as the fallback while the stream
// is down). A click on a hit opens the core's PDF viewer beside the log at
// that spot (LocalBib.components.PdfPreview, contract 2.6) — it does not
// navigate. Only "Zur Detailansicht" goes to /paper/{id}?page&find&back; the
// core's back key returns here, and scroll position, the open hit and the
// viewed hits come back from sessionStorage. A core without PdfPreview gets
// the detail route straight from the click.

const LACITE_POLL_MS = 5000;
const LACITE_WIDE_PX = 1100;
const LACITE_POPUP = 'lacite-live';
const LACITE_LOOKUP_CHUNK = 40;

function laciteIsPopup() {
    try {
        return window.name === LACITE_POPUP;
    } catch (e) {
        return false;
    }
}

const LACITE_LIVE_COMPONENTS = {};
if (LB.components && LB.components.PdfPreview) LACITE_LIVE_COMPONENTS.PdfPreview = LB.components.PdfPreview;

const LaCiteLive = {
    components: LACITE_LIVE_COMPONENTS,
    data() {
        return {
            entries: [], client: null, loading: true, loadError: '', notice: '',
            ids: {},        // citekey -> { id, has_pdf, missing } (the entry's own or GET /api/papers/lookup)
            open: null,     // { entry, hit } shown in the PDF pane
            viewed: {},     // 'entry:hit' -> true
            pdf: null,      // the viewer's state
            popup: laciteIsPopup(),
            clearing: false,
            // Schlussberichte (submit_report)
            tab: 'log',     // 'log' | 'reports'
            reports: [], reportsLoaded: false, reportsError: '', persistent: true,
            report: null,   // the open report, full
            filter: 'all',  // all | supported | check | unsupported
            query: '', searchHits: null,
            srcOpen: null,  // { report, claim, src } shown in the PDF pane
            copied: '',
        };
    },
    computed: {
        hasViewer() { return !!LACITE_LIVE_COMPONENTS.PdfPreview; },
        newestFirst() { return this.entries.slice().reverse(); },
        statusLine() {
            const n = this.entries.length;
            const calls = n === 1 ? '1 Aufruf' : n + ' Aufrufe';
            const since = n && laciteClock(this.entries[0].at) ? ' seit ' + laciteClock(this.entries[0].at) : '';
            const who = this.client && this.client.name ? this.client.name + ' verbunden' : 'Noch kein Agent verbunden';
            return who + ' · ' + calls + since + ' · nur im Speicher';
        },
        cur() {
            if (this.tab === 'reports') return this.curSource;
            if (!this.open) return null;
            const entry = this.entries.find((e) => e.id === this.open.entry);
            const h = entry && entry.hits && entry.hits[this.open.hit];
            if (!h) return null;
            const known = this.ids[h.citekey] || {};
            return {
                ...h, key: h.citekey, pageText: laciteHitPage(h),
                paperId: h.paper_id || known.id || null,
                pdfPage: Number(h.pdf_page) || 1,
                find: h.pdf_page ? laciteFindText(h.text) : '',
            };
        },
        curSource() {
            const o = this.srcOpen;
            const r = this.report;
            if (!o || !r || r.id !== o.report) return null;
            const claim = (r.claims || []).find((c) => c.n === o.claim);
            const src = claim && claim.sources && claim.sources[o.src];
            if (!src) return null;
            const known = this.ids[src.citekey] || {};
            const pdfPage = Number(src.pdf_page) || Number(src.pdf_page_hint) || 1;
            const text = src.state === 'verified' ? laciteFindText(src.passage) : '';
            return {
                key: src.citekey, title: src.title || known.title || '', pageText: laciteSourcePage(src),
                paperId: src.paper_id || known.id || null, pdfPage, find: text,
                page: src.page, pdf_page: src.pdf_page || src.pdf_page_hint || null, text,
            };
        },
        emptyText() {
            return this.tab === 'reports'
                ? 'Klick auf eine Textstelle, dann öffnet sich die PDF hier an der Stelle.'
                : 'Klick auf einen Treffer, dann öffnet sich die PDF hier an der Stelle.';
        },
        reportCounts() { return (this.report && this.report.counts) || {}; },
        filteredClaims() {
            const claims = (this.report && this.report.claims) || [];
            return this.filter === 'all' ? claims : claims.filter((c) => c.status === this.filter);
        },
        filters() {
            const c = this.reportCounts;
            return [
                { id: 'all', label: 'Alle', n: c.total || 0, cls: '' },
                { id: 'supported', label: 'Belegt', n: c.supported || 0, cls: 'is-ok' },
                { id: 'check', label: 'Prüfen', n: c.check || 0, cls: 'is-warn' },
                { id: 'unsupported', label: 'Ohne Beleg', n: c.unsupported || 0, cls: 'is-none' },
            ];
        },
        hitGroups() { return laciteGroupHits(this.searchHits); },
        hitsLine() { return laciteHitsLine(this.searchHits); },
        pdfLine() {
            const s = this.pdf || {};
            if (s.page) return 'PDF-S. ' + s.page + (s.pages ? ' / ' + s.pages : '');
            return this.cur && this.cur.pdf_page ? 'PDF-S. ' + this.cur.pdf_page : '';
        },
        pdfNote() {
            const c = this.cur;
            const s = this.pdf || {};
            if (s.status === 'nopdf') return 'zu diesem Eintrag gibt es keine PDF';
            if (s.status === 'failed') return 'die PDF ließ sich nicht öffnen';
            const parts = [];
            if (c && !c.page && c.pdf_page) parts.push('keine gedruckte Seite');
            if (s.found === true) parts.push('Absatz gefunden');
            else if (s.found === false) parts.push('nur die Seite – Absatz nicht eindeutig');
            else if (c && c.find) parts.push('sucht die Stelle …');
            return parts.join(' · ');
        },
    },
    created() {
        const saved = laciteLoadLive();
        if (saved) {
            for (const k of Array.isArray(saved.viewed) ? saved.viewed : []) this.viewed[k] = true;
            if (saved.open && typeof saved.open === 'object') this.open = { entry: saved.open.entry, hit: saved.open.hit };
            this._restoreScroll = Number(saved.scroll) || 0;
            if (saved.tab === 'reports') this.tab = 'reports';
            this._restoreReport = Number(saved.report) || 0;
            if (saved.srcOpen && typeof saved.srcOpen === 'object') this._restoreSrc = saved.srcOpen;
        }
    },
    async mounted() {
        this.listen();
        await this.load();
        // An open hit whose call is gone (cleared, restarted) closes.
        if (this.open && this.tab === 'log' && !this.cur) this.open = null;
        if (this.tab === 'reports') {
            await this.loadReports();
            if (this._restoreReport) await this.openReport(this._restoreReport, 0, this._restoreSrc);
        }
        await this.$nextTick();
        if (this._restoreScroll && this.$refs.log) this.$refs.log.scrollTop = this._restoreScroll;
        this._restoreScroll = 0;
    },
    beforeUnmount() {
        this.remember();
        if (this._es) {
            try { this._es.close(); } catch (e) { /* gone already */ }
        }
        this.stopPoll();
        if (this._searchTimer) clearTimeout(this._searchTimer);
    },
    methods: {
        remember() {
            laciteSaveLive({
                scroll: this.$refs.log ? this.$refs.log.scrollTop : 0,
                open: this.open,
                viewed: Object.keys(this.viewed),
                tab: this.tab,
                report: this.report ? this.report.id : 0,
                srcOpen: this.srcOpen,
            });
        },
        // ── Schlussberichte ──────────────────────────────────────────────
        showTab(tab) {
            if (this.tab === tab) return;
            this.tab = tab;
            this.pdf = null;
            if (tab === 'reports' && !this.reportsLoaded) this.loadReports();
            // Replaced while another tab was shown: fetch the new version now.
            if (tab === 'reports' && this.report && this._staleReport === this.report.id) {
                this.openReport(this.report.id, 0, this.srcOpen);
            }
            this.remember();
        },
        async loadReports() {
            try {
                const out = await lcApi('/reports');
                this.reports = out.reports || [];
                this.persistent = out.persistent !== false;
                this.reportsError = '';
            } catch (e) {
                this.reportsError = laciteErrorText(e);
            } finally {
                this.reportsLoaded = true;
            }
        },
        // Open a report; with `claimN` scroll to that claim and open its first source.
        async openReport(id, claimN, src) {
            this.notice = '';
            try {
                const report = await lcApi('/reports/' + encodeURIComponent(id));
                if (this._staleReport === id) this._staleReport = 0;
                this.tab = 'reports';
                this.report = report;
                this.filter = 'all';
                this.srcOpen = null;
                this.pdf = null;
                this.resolveSources(report);
            } catch (e) {
                this.notice = laciteErrorText(e);
                this.report = null;
                return;
            }
            if (src && src.report === id) this.srcOpen = { report: id, claim: src.claim, src: src.src };
            await this.$nextTick();
            if (claimN) {
                const el = this.$el && this.$el.querySelector && this.$el.querySelector('[data-claim="' + claimN + '"]');
                if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' });
                const claim = (this.report.claims || []).find((c) => c.n === claimN);
                const first = claim ? (claim.sources || []).findIndex((x) => laciteSourceTag(x).open) : -1;
                if (first >= 0) this.openSource(claim, first);
            }
            this.remember();
        },
        backToList() {
            this.report = null;
            this.srcOpen = null;
            this.pdf = null;
            this.loadReports();
            this.remember();
        },
        onSearch() {
            if (this._searchTimer) clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => this.runSearch(), 250);
        },
        async runSearch() {
            const q = this.query.trim();
            if (!q) {
                this.searchHits = null;
                return;
            }
            try {
                const out = await lcApi('/reports?q=' + encodeURIComponent(q));
                if (q === this.query.trim()) this.searchHits = out.hits || [];
            } catch (e) {
                this.reportsError = laciteErrorText(e);
            }
        },
        resolveSources(report) {
            const keys = new Set();
            for (const c of report.claims || []) {
                for (const src of c.sources || []) {
                    if (!src.citekey) continue;
                    if (src.paper_id) this.ids[src.citekey] = { id: src.paper_id, has_pdf: src.has_pdf, missing: false };
                    else if (!(src.citekey in this.ids)) keys.add(src.citekey);
                }
            }
            if (keys.size) return this.resolve([...keys]);
            return Promise.resolve();
        },
        statusOf(claim) { return laciteReportStatus(claim.status); },
        srcCard(claim, src, i) {
            const tag = laciteSourceTag(src);
            const known = this.ids[src.citekey] || {};
            const hasPdf = src.has_pdf === true || src.has_pdf === false ? src.has_pdf : known.has_pdf;
            const isOpen = !!this.srcOpen && this.report && this.srcOpen.report === this.report.id
                && this.srcOpen.claim === claim.n && this.srcOpen.src === i;
            const open = tag.open && hasPdf !== false && !known.missing;
            return { tag, isOpen, open, page: laciteSourcePage(src), warning: laciteSourceWarning(src),
                noPdf: tag.open && !open };
        },
        async openSource(claim, i) {
            const src = claim.sources[i];
            this.notice = '';
            let paperId = src.paper_id || (this.ids[src.citekey] || {}).id;
            if (!paperId) {
                await this.resolve([src.citekey]);
                paperId = (this.ids[src.citekey] || {}).id;
            }
            if (!paperId) {
                this.notice = src.citekey + ' gibt es nicht (mehr) in der Bibliothek.';
                return;
            }
            const pdfPage = Number(src.pdf_page) || Number(src.pdf_page_hint) || null;
            if (!this.hasViewer) {
                this.goDetail(paperId, { pdf_page: pdfPage, text: src.state === 'verified' ? src.passage : '' });
                return;
            }
            this.srcOpen = { report: this.report.id, claim: claim.n, src: i };
            this.pdf = null;
            this.widen();
            this.remember();
        },
        async copyCite(text) {
            this.copied = (await laciteCopy(text)) ? text : '';
            if (this.copied) setTimeout(() => { if (this.copied === text) this.copied = ''; }, 2000);
        },
        markdownUrl(report) { return BASE + '/reports/' + encodeURIComponent(report.id) + '/markdown'; },
        reportMeta(report) { return laciteReportMeta(report); },
        markParts(text) { return laciteMarkParts(text, this.query); },
        hitSource(claim) {
            const src = (claim.sources || []).find((x) => x.state === 'verified') || (claim.sources || [])[0];
            return src ? src.citekey + (laciteSourcePage(src) ? ' · ' + laciteSourcePage(src) : '') : '';
        },
        async load() {
            try {
                const out = await lcApi('/session');
                this.entries = out.entries || [];
                this.client = out.client || this.client;
                this.loadError = '';
                this.resolveFrom(this.entries);
            } catch (e) {
                if (!this.entries.length) this.loadError = laciteErrorText(e);
            } finally {
                this.loading = false;
            }
        },
        listen() {
            if (typeof window.EventSource !== 'function') {
                this.startPoll();
                return;
            }
            try {
                const es = new window.EventSource(BASE + '/events');
                this._es = es;
                es.onmessage = (ev) => {
                    let msg = null;
                    try { msg = JSON.parse(ev.data); } catch (e) { return; }
                    this.onEvent(msg);
                };
                // While the stream is down, GET /session every few seconds;
                // once it is back, one reload catches what was missed.
                es.onerror = () => this.startPoll();
                es.onopen = () => {
                    if (!this._poll) return;
                    this.stopPoll();
                    this.load();
                };
            } catch (e) {
                this.startPoll();
            }
        },
        onEvent(msg) {
            if (!msg || typeof msg !== 'object') return;
            if (msg.type === 'mcp-call') this.add(msg.entry);
            else if (msg.type === 'session-cleared') this.resetLocal();
        },
        startPoll() {
            if (this._poll) return;
            this._poll = setInterval(() => this.load(), LACITE_POLL_MS);
        },
        stopPoll() {
            if (this._poll) clearInterval(this._poll);
            this._poll = null;
        },
        add(entry) {
            if (!entry || this.entries.some((e) => e.id === entry.id)) return;
            this.entries.push(entry);
            if (this.entries.length > 200) this.entries.splice(0, this.entries.length - 200);
            if (entry.client && entry.client.name) this.client = { ...entry.client, at: entry.at };
            this.resolveFrom([entry]);
            if (entry.tool === 'submit_report' && entry.report) {
                if (this.reportsLoaded) this.loadReports();
                // Refresh the open report in place: stay on the tab the user is
                // on and keep the source shown in the PDF pane.
                if (this.report && this.report.id === entry.report.id) {
                    if (this.tab === 'reports') this.openReport(entry.report.id, 0, this.srcOpen);
                    else this._staleReport = entry.report.id;
                }
                if (this.query.trim()) this.runSearch();
            }
        },
        resolveFrom(entries) {
            const keys = new Set();
            for (const e of entries) {
                for (const h of e.hits || []) {
                    if (!h.citekey) continue;
                    if (h.paper_id) this.ids[h.citekey] = { id: h.paper_id, has_pdf: h.has_pdf, missing: false };
                    else if (!(h.citekey in this.ids)) keys.add(h.citekey);
                }
            }
            if (keys.size) return this.resolve([...keys]);
            return Promise.resolve();
        },
        // Item id + PDF of each citekey via the core's lookup (contract, reads only).
        async resolve(keys) {
            for (let i = 0; i < keys.length; i += LACITE_LOOKUP_CHUNK) {
                const chunk = keys.slice(i, i + LACITE_LOOKUP_CHUNK);
                const params = chunk.map((k) => 'keys=' + encodeURIComponent(k)).join('&');
                try {
                    const out = await LB.api('/api/papers/lookup?' + params);
                    const found = {};
                    for (const p of (out && out.papers) || []) found[p.cite_key] = p;
                    for (const k of chunk) {
                        this.ids[k] = found[k]
                            ? { id: found[k].id, has_pdf: !!found[k].has_pdf, missing: false }
                            : { id: null, has_pdf: false, missing: true };
                    }
                } catch (e) { /* stays unknown: a click asks again */ }
            }
        },
        showHits(entry) { return LACITE_HIT_TOOLS.includes(entry.tool) && (entry.hits || []).length > 0; },
        cards(entry) {
            return (entry.hits || []).map((h, i) => {
                const known = this.ids[h.citekey] || {};
                const hasPdf = h.has_pdf === true || h.has_pdf === false ? h.has_pdf : known.has_pdf;
                const k = entry.id + ':' + i;
                const isOpen = !!this.open && this.open.entry === entry.id && this.open.hit === i;
                const seen = !isOpen && !!this.viewed[k];
                const link = !!h.citekey && hasPdf !== false;
                let tag = '';
                if (!link) tag = known.missing ? 'nicht in der Bibliothek' : 'keine PDF';
                else if (isOpen) tag = 'offen';
                else if (!h.page && h.pdf_page) tag = 'keine gedruckte Seite';
                else if (seen) tag = 'angesehen';
                return { h, i, k, link, isOpen, seen, tag, page: laciteHitPage(h) };
            });
        },
        async idOf(h) {
            if (h.paper_id) return h.paper_id;
            if (!(this.ids[h.citekey] || {}).id) await this.resolve([h.citekey]);
            return (this.ids[h.citekey] || {}).id || null;
        },
        async openHit(entry, i) {
            const h = entry.hits[i];
            this.notice = '';
            const paperId = await this.idOf(h);
            if (!paperId) {
                this.notice = h.citekey + ' gibt es nicht (mehr) in der Bibliothek.';
                return;
            }
            this.viewed[entry.id + ':' + i] = true;
            if (!this.hasViewer) {   // a core before contract 2.6: the detail view instead
                this.goDetail(paperId, h);
                return;
            }
            this.open = { entry: entry.id, hit: i };
            this.pdf = null;
            this.widen();
            this.remember();
        },
        closePdf() {
            if (this.tab === 'reports') this.srcOpen = null;
            else this.open = null;
            this.pdf = null;
            this.remember();
        },
        onPdfState(state) { this.pdf = state; },
        toDetail() {
            const c = this.cur;
            if (c && c.paperId) this.goDetail(c.paperId, c);
        },
        goDetail(paperId, h) {
            const query = {};
            if (h.pdf_page) {
                query.page = String(h.pdf_page);
                const find = laciteFindText(h.text);
                if (find) query.find = find;
            }
            query.back = '/lacite/live';
            this.remember();
            this.$router.push({ path: `/paper/${paperId}`, query });
        },
        // The PDF needs room: a narrow popup grows once (a browser may refuse).
        widen() {
            if (!this.popup || this._widened) return;
            this._widened = true;
            try {
                if (window.innerWidth < LACITE_WIDE_PX) {
                    window.resizeTo(Math.max(1180, window.outerWidth || 0), window.outerHeight || 900);
                }
            } catch (e) { /* not ours to resize */ }
        },
        openWindow() {
            try {
                const url = window.location.origin + window.location.pathname + '#/lacite/live';
                window.open(url, LACITE_POPUP, 'popup=yes,width=480,height=900');
            } catch (e) { /* blocked: the page works here as well */ }
        },
        async clear() {
            this.clearing = true;
            try {
                await lcApi('/session', { method: 'DELETE' });
                this.resetLocal();
            } catch (e) {
                this.notice = laciteErrorText(e);
            } finally {
                this.clearing = false;
            }
        },
        resetLocal() {
            this.entries = [];
            this.open = null;
            this.viewed = {};
            this.pdf = null;
            this.remember();
        },
    },
    template: `
<div class="lacite-live" :class="{ 'has-open': !!cur, 'is-popup': popup }" data-testid="lacite-live">
  <div class="lacite-live-log" :class="{ 'is-wide': tab === 'reports' }">
    <header class="lacite-live-head">
      <div class="lacite-row-between">
        <div class="lb-eyebrow">LaCite · Live</div>
        <div class="lb-tabs lacite-live-tabs" role="tablist" aria-label="Ansicht">
          <button type="button" role="tab" class="lb-tab" :class="{ 'is-active': tab === 'log' }" :aria-selected="tab === 'log' ? 'true' : 'false'"
                  data-testid="lacite-tab-log" @click="showTab('log')"><span class="lb-tab-label">Verlauf</span><span class="lb-tab-count">{{ entries.length }}</span></button>
          <button type="button" role="tab" class="lb-tab" :class="{ 'is-active': tab === 'reports' }" :aria-selected="tab === 'reports' ? 'true' : 'false'"
                  data-testid="lacite-tab-reports" @click="showTab('reports')"><span class="lb-tab-label">Berichte</span><span v-if="reportsLoaded" class="lb-tab-count">{{ reports.length }}</span></button>
        </div>
      </div>
      <template v-if="tab === 'log'">
        <h1 class="lacite-live-title">Was der Agent nachschlägt</h1>
        <p class="lacite-live-status"><span class="lacite-live-dot" :class="{ 'is-on': client && client.name }" aria-hidden="true"></span>{{ statusLine }}</p>
        <div class="lacite-actions">
          <button v-if="!popup" type="button" class="lb-key lacite-key-sm" data-testid="lacite-live-window" @click="openWindow">Im eigenen Fenster öffnen</button>
          <button type="button" class="lb-key lacite-key-sm" data-testid="lacite-live-clear" :disabled="clearing || !entries.length" @click="clear">Sitzung leeren</button>
        </div>
      </template>
      <template v-else-if="report">
        <button type="button" class="lacite-back" data-testid="lacite-report-back" @click="backToList">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>Alle Berichte</button>
        <h1 class="lacite-live-title lacite-report-title" data-testid="lacite-report-title">{{ report.title }}</h1>
        <p class="lacite-live-status">{{ reportMeta(report) }}</p>
        <div class="lacite-row-between lacite-report-bar">
          <div class="lacite-filters" role="group" aria-label="Aussagen filtern">
            <button v-for="f in filters" :key="f.id" type="button" class="lacite-chip" :class="[f.cls, { 'is-on': filter === f.id }]"
                    :aria-pressed="filter === f.id ? 'true' : 'false'" :data-filter="f.id" @click="filter = f.id">
              <span v-if="f.cls" class="lacite-status-dot" :class="f.cls" aria-hidden="true"></span>{{ f.label }} {{ f.n }}</button>
          </div>
          <a class="lb-key lacite-key-sm" :href="markdownUrl(report)" download data-testid="lacite-report-md">Als Markdown speichern</a>
        </div>
      </template>
      <template v-else>
        <h1 class="lacite-live-title">Schlussberichte</h1>
        <label for="lacite-report-search" class="lacite-search-label">In allen Aussagen, Zitaten und Schlüsseln suchen</label>
        <div class="lacite-search">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/></svg>
          <input id="lacite-report-search" v-model="query" type="search" class="lacite-search-input" autocomplete="off"
                 data-testid="lacite-report-search" @input="onSearch">
          <span v-if="query.trim() && searchHits" class="lb-mono lacite-search-count">{{ hitsLine }}</span>
        </div>
      </template>
    </header>
    <div v-if="tab === 'reports'" class="lacite-live-scroll lacite-reports" data-testid="lacite-reports">
      <p v-if="notice" class="lacite-error lacite-live-notice" role="alert">{{ notice }}</p>
      <template v-if="report">
        <p v-if="report.summary" class="lacite-report-summary">{{ report.summary }}</p>
        <p v-if="!filteredClaims.length" class="lacite-muted lacite-live-none">Keine Aussage in diesem Filter.</p>
        <article v-for="c in filteredClaims" :key="c.n" class="lacite-claim" :data-claim="c.n">
          <span class="lb-mono lacite-claim-n">{{ c.n }}</span>
          <div class="lacite-claim-body">
            <div class="lacite-claim-head">
              <p class="lacite-claim-text">{{ c.claim }}</p>
              <span class="lacite-status" :class="statusOf(c).cls"><span class="lacite-status-dot" :class="statusOf(c).cls" aria-hidden="true"></span>{{ statusOf(c).label }}</span>
            </div>
            <template v-for="(src, i) in c.sources" :key="i">
              <template v-for="card in [srcCard(c, src, i)]" :key="'card' + i">
                <button v-if="card.open" type="button" class="lacite-hit lacite-src" :class="{ 'is-open': card.isOpen }"
                        :aria-pressed="card.isOpen ? 'true' : 'false'" :data-src="c.n + ':' + i" @click="openSource(c, i)">
                  <span class="lacite-hit-head">
                    <span class="lacite-cite">{{ src.citekey }}<span v-if="card.page" class="lacite-hit-page"> · {{ card.page }}</span></span>
                    <span class="lb-mono lacite-src-tag" :class="card.tag.cls">{{ card.tag.text }}</span>
                  </span>
                  <span v-if="src.quote" class="lacite-excerpt is-full">„{{ src.quote }}“</span>
                </button>
                <div v-else class="lacite-hit lacite-src is-off" :data-src="c.n + ':' + i">
                  <span class="lacite-hit-head">
                    <span class="lacite-cite">{{ src.citekey }}<span v-if="card.page" class="lacite-hit-page"> · {{ card.page }}</span></span>
                    <span class="lb-mono lacite-src-tag" :class="card.tag.cls">{{ card.noPdf ? 'keine PDF' : card.tag.text }}</span>
                  </span>
                  <span v-if="src.quote" class="lacite-excerpt is-full">„{{ src.quote }}“</span>
                </div>
                <p v-if="card.warning" class="lacite-src-warn">{{ card.warning }}</p>
              </template>
              <p v-if="src.note" class="lacite-agent-note"><span class="lacite-agent-who">Agent:</span> {{ src.note }}</p>
              <div v-if="src.state === 'verified'" class="lacite-cite-row">
                <code class="lacite-cite-code">{{ src.cite }}</code>
                <button type="button" class="lacite-link-btn" @click="copyCite(src.cite)">{{ copied === src.cite ? 'Kopiert' : 'Kopieren' }}</button>
              </div>
            </template>
            <div v-if="!c.sources.length" class="lacite-nosrc">
              <span>Keine Stelle in der Bibliothek gefunden.</span>
              <span v-if="c.searched && c.searched.length" class="lacite-muted">Gesucht: <template v-for="(q, qi) in c.searched" :key="qi"><span class="lacite-part-quote">{{ q }}</span><span v-if="qi < c.searched.length - 1">, </span></template></span>
            </div>
            <p v-if="c.note" class="lacite-agent-note"><span class="lacite-agent-who">Agent:</span> {{ c.note }}</p>
          </div>
        </article>
      </template>
      <template v-else>
        <p v-if="reportsError" class="lacite-error lacite-live-none" role="alert">{{ reportsError }}</p>
        <p v-else-if="!reportsLoaded" class="lacite-muted lacite-live-none">Lädt …</p>
        <p v-if="reportsLoaded && !persistent" class="lacite-muted lacite-live-none">Nur bis zum nächsten Start gespeichert: dieses LocalBib ist zu alt für eigene Add-on-Daten (Contract 2.7).</p>
        <template v-if="query.trim() && searchHits">
          <section v-for="g in hitGroups" :key="g.id" class="lacite-hit-group">
            <div class="lacite-hit-group-head">
              <button type="button" class="lacite-report-link" @click="openReport(g.id)">{{ g.title }}</button>
              <span class="lb-mono lacite-muted lacite-when">{{ reportMeta({ created_at: g.created_at }) }}</span>
            </div>
            <button v-for="c in g.claims" :key="c.n" type="button" class="lacite-claim-hit" :data-hit-claim="g.id + ':' + c.n" @click="openReport(g.id, c.n)">
              <span class="lb-mono lacite-claim-n">{{ c.n }}</span>
              <span class="lacite-claim-hit-body">
                <span class="lacite-claim-hit-text"><template v-for="(part, pi) in markParts(c.claim)" :key="pi"><mark v-if="part.mark" class="lacite-mark">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></span>
                <span class="lacite-cite">{{ hitSource(c) }} <span :class="['lacite-status', statusOf(c).cls]">· {{ statusOf(c).label.toLowerCase() }}</span></span>
              </span>
            </button>
          </section>
        </template>
        <template v-if="reportsLoaded && !reportsError">
          <h2 class="lacite-label lacite-reports-label">Alle Berichte</h2>
          <p v-if="!reports.length" class="lacite-muted lacite-live-none">Noch keine Berichte. Am Ende einer Schreibaufgabe gibt dein Assistent hier seinen Schlussbericht ab: jede zu belegende Aussage mit der passenden Textstelle.</p>
          <button v-for="r in reports" :key="r.id" type="button" class="lacite-report-row" :data-report="r.id" @click="openReport(r.id)">
            <span class="lacite-report-row-main">
              <span class="lacite-report-row-title">{{ r.title }}</span>
              <span class="lacite-muted lacite-when">{{ reportMeta(r) }}</span>
            </span>
            <span class="lacite-counts" :aria-label="(r.counts.supported || 0) + ' belegt, ' + (r.counts.check || 0) + ' prüfen, ' + (r.counts.unsupported || 0) + ' ohne Beleg'">
              <span><span class="lacite-status-dot is-ok" aria-hidden="true"></span>{{ r.counts.supported || 0 }}</span>
              <span><span class="lacite-status-dot is-warn" aria-hidden="true"></span>{{ r.counts.check || 0 }}</span>
              <span><span class="lacite-status-dot is-none" aria-hidden="true"></span>{{ r.counts.unsupported || 0 }}</span>
            </span>
          </button>
        </template>
      </template>
    </div>
    <div v-else ref="log" class="lacite-live-scroll" data-testid="lacite-live-log">
      <p v-if="notice" class="lacite-error lacite-live-notice" role="alert">{{ notice }}</p>
      <p v-if="loading" class="lacite-muted lacite-live-none">Lädt …</p>
      <p v-else-if="loadError" class="lacite-error lacite-live-none" role="alert">{{ loadError }}</p>
      <p v-else-if="!entries.length" class="lacite-muted lacite-live-none">Noch keine Aufrufe. Sobald dein Assistent über LaCite nachschlägt, steht es hier – mit jeder Fundstelle.</p>
      <section v-for="entry in newestFirst" :key="entry.id" class="lacite-call" :data-entry="entry.id">
        <span class="lb-mono lacite-call-time">{{ laciteTime(entry.at) }}</span>
        <div class="lacite-call-body">
          <p class="lacite-call-text"><span v-for="(part, pi) in laciteCallParts(entry)" :key="pi" :class="'lacite-part-' + part.kind">{{ part.text }}</span></p>
          <p v-if="entry.error" role="status" class="lacite-call-error">Fehler: {{ entry.error }}</p>
          <button v-if="entry.report" type="button" class="lacite-report-card" :data-open-report="entry.report.id" @click="openReport(entry.report.id)">
            <span class="lacite-report-card-title">{{ entry.report.title }}</span>
            <span class="lacite-counts">
              <span><span class="lacite-status-dot is-ok" aria-hidden="true"></span>{{ entry.report.counts.supported || 0 }} belegt</span>
              <span><span class="lacite-status-dot is-warn" aria-hidden="true"></span>{{ entry.report.counts.check || 0 }} prüfen</span>
              <span><span class="lacite-status-dot is-none" aria-hidden="true"></span>{{ entry.report.counts.unsupported || 0 }} ohne</span>
            </span>
            <span class="lacite-report-card-go">Bericht öffnen
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></span>
          </button>
          <template v-if="showHits(entry)">
            <template v-for="c in cards(entry)" :key="c.k">
              <button v-if="c.link" type="button" class="lacite-hit" :class="{ 'is-open': c.isOpen, 'is-seen': c.seen }"
                      :aria-pressed="c.isOpen ? 'true' : 'false'" :data-hit="c.k" @click="openHit(entry, c.i)">
                <span class="lacite-hit-head">
                  <span class="lacite-cite">{{ c.h.citekey }}<span v-if="c.page" class="lacite-hit-page"> · {{ c.page }}</span></span>
                  <span class="lb-mono lacite-hit-tag">{{ c.tag }}</span>
                </span>
                <span v-if="c.h.text || c.h.title" class="lacite-excerpt">{{ c.h.text || c.h.title }}</span>
              </button>
              <div v-else class="lacite-hit is-off" :data-hit="c.k">
                <span class="lacite-hit-head">
                  <span class="lacite-cite">{{ c.h.citekey }}<span v-if="c.page" class="lacite-hit-page"> · {{ c.page }}</span></span>
                  <span class="lb-mono lacite-hit-tag">{{ c.tag }}</span>
                </span>
                <span v-if="c.h.text || c.h.title" class="lacite-excerpt">{{ c.h.text || c.h.title }}</span>
              </div>
            </template>
          </template>
        </div>
      </section>
    </div>
  </div>

  <section v-if="cur" class="lacite-live-pane" aria-label="Fundstelle" data-testid="lacite-live-pdf">
    <div class="lacite-pane">
      <header class="lacite-pane-head">
        <div class="lacite-pane-title">
          <span class="lacite-cite">{{ cur.key }}<span v-if="cur.pageText" class="lacite-hit-page"> · {{ cur.pageText }}</span></span>
          <span class="lacite-pane-work lb-truncate" :title="cur.title">{{ cur.title }}</span>
        </div>
        <div class="lacite-actions">
          <button type="button" class="lb-key" data-testid="lacite-to-detail" :disabled="!cur.paperId" @click="toDetail">Zur Detailansicht
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>
          </button>
          <button type="button" class="lb-key lb-key-icon" aria-label="PDF schließen" title="PDF schließen" data-testid="lacite-pdf-close" @click="closePdf">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
          </button>
        </div>
      </header>
      <div class="lacite-pane-bar">
        <span class="lb-mono">{{ pdfLine }}</span>
        <span class="lb-mono">{{ pdfNote }}</span>
      </div>
      <div class="lacite-pane-body">
        <pdf-preview v-if="cur.paperId" :key="cur.paperId" :paper-id="cur.paperId" :page="cur.pdfPage" :find="cur.find" @state="onPdfState"></pdf-preview>
      </div>
    </div>
  </section>
  <div v-else class="lacite-live-empty">
    <span class="lacite-live-empty-mark" aria-hidden="true">[ ]</span>
    <span>{{ emptyText }}</span>
  </div>
</div>`,
};
LaCiteLive.methods.laciteTime = laciteTime;
LaCiteLive.methods.laciteCallParts = laciteCallParts;

// ── The page ─────────────────────────────────────────────────────────────────

const LaCiteHome = {
    components: { LaCiteTutorial, LaCiteDialog, LaCiteCard, LaCiteAssistant },
    data() {
        return {
            loading: true, loadError: '', connections: [], status: null,
            tutorial: false, dialog: null, now: Date.now(), timer: null, toast: '',
        };
    },
    computed: {
        empty() { return !this.loading && !this.loadError && this.connections.length === 0; },
        showTutorial() { return this.empty || this.tutorial; },
        countText() {
            const n = this.connections.length;
            return (n === 1 ? '1 Verbindung' : n + ' Verbindungen') + ' · deine Bibliothek in deinem LaTeX-Editor';
        },
    },
    mounted() {
        this.load();
        this.timer = setInterval(() => { this.now = Date.now(); this.load(true); }, REFRESH_MS);
    },
    unmounted() { if (this.timer) clearInterval(this.timer); },
    methods: {
        async load(quiet) {
            try {
                const [list, status] = await Promise.all([lcApi('/connections'), lcApi('/status')]);
                this.connections = list.connections || [];
                this.status = status;
                this.loadError = '';
            } catch (e) {
                if (!quiet) this.loadError = laciteErrorText(e);
            } finally {
                this.loading = false;
            }
        },
        openNew() { this.tutorial = false; this.dialog = { mode: 'new', conn: null }; },
        openEdit(conn) { this.dialog = { mode: 'edit', conn }; },
        openSource(conn) { this.dialog = { mode: 'source', conn }; },
        closeDialog() { this.dialog = null; this.load(true); },
        onSaved(conn) {
            const i = this.connections.findIndex((c) => c.id === conn.id);
            if (i >= 0) this.connections.splice(i, 1, conn); else this.connections.push(conn);
        },
        toAssistant() {
            this.dialog = null;
            this.tutorial = false;
            this.$nextTick(() => {
                const el = document.getElementById('lacite-ki');
                if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        },
        onChanged(conn) { this.onSaved(conn); this.now = Date.now(); },
        onRemoved(conn) {
            this.connections = this.connections.filter((c) => c.id !== conn.id);
            this.toast = 'Verbindung „' + conn.name + '“ getrennt. ' + conn.filename + ' liegt weiter im Ordner.';
            setTimeout(() => { this.toast = ''; }, 6000);
        },
    },
    template: `
<div class="lb-view lacite-view">
  <header class="lb-page-head lacite-head">
    <div class="lb-page-head-l">
      <div class="lb-eyebrow">Add-on</div>
      <h1 class="lb-page-title">LaCite</h1>
      <p v-if="showTutorial" class="lacite-lede">Deine Bibliothek in deinem LaTeX-Editor. LaCite legt eine <span class="lb-mono">.bib</span>-Datei in deinen Projektordner, hält sie aktuell – und lässt deinen KI-Assistenten Textstellen mit Seitenzahl zitieren.</p>
      <p v-else-if="!loading" class="lb-page-meta">{{ countText }}</p>
    </div>
    <div class="lb-page-head-r">
      <button type="button" class="lb-key lb-key-icon lacite-help" aria-label="Anleitung anzeigen" title="Anleitung anzeigen"
              :aria-pressed="showTutorial" @click="tutorial = !tutorial">?</button>
      <router-link to="/lacite/live" class="lb-key" data-testid="lacite-live-link">Live-Sitzung</router-link>
      <button v-if="!showTutorial" type="button" class="lb-key lb-key-primary" @click="openNew">+ Neue Verbindung</button>
    </div>
  </header>

  <p v-if="loading" class="lacite-muted">Lädt …</p>
  <p v-else-if="loadError" class="lacite-error" role="alert">{{ loadError }}</p>

  <la-cite-tutorial v-if="showTutorial && !loadError" :has-connections="connections.length > 0"
                    @connect="openNew" @close="tutorial = false"></la-cite-tutorial>

  <div v-else-if="!loading && !loadError" class="lacite-overview">
    <section aria-labelledby="lacite-conns" class="lacite-section">
      <h2 id="lacite-conns" class="lacite-label">Verbindungen</h2>
      <la-cite-card v-for="c in connections" :key="c.id" :conn="c" :now="now"
                    @edit="openEdit" @source="openSource" @changed="onChanged" @removed="onRemoved"></la-cite-card>
    </section>
    <la-cite-assistant :mcp="status && status.mcp" :now="now"></la-cite-assistant>
  </div>

  <div v-if="toast" class="lacite-toast" role="status">{{ toast }}</div>

  <la-cite-dialog v-if="dialog" :mode="dialog.mode" :conn="dialog.conn"
                  @close="closeDialog" @saved="onSaved" @assistant="toAssistant"></la-cite-dialog>
</div>`,
};

window.laCitePure = {
    laciteCount, laciteAgo, laciteStatus, laciteSourceLabel, laciteMcpUrl,
    laciteAssistantSnippet, laciteMissingText, laciteErrorText, LACITE_ERROR_TEXT,
    laciteMissingGroups, laciteFilesLine, laciteImportName, laciteIncludeLine,
    laciteHitPage, laciteCallParts, laciteFindText,
    laciteWhen, laciteReportStatus, laciteSourceTag, laciteSourcePage, laciteSourceWarning,
    laciteReportMeta, laciteGroupHits, laciteHitsLine, laciteMarkParts,
};

LB.registerPlugin({
    id: 'lacite',
    views: { 'lacite.home': LaCiteHome, 'lacite.live': LaCiteLive },
    subroutes: [{ path: '/lacite/live', view: 'lacite.live' }],
});
})();
