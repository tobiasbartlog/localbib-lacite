// lacite — the frontend of the LaCite Bundle (Add-on Contract 2).
//
// One script per Bundle. It holds the one LaCite page (/lacite): the tutorial
// that is also the empty state, the connection cards with every state
// (Aktuell, Wird aktualisiert, Pausiert, Fehler), the three-step dialog
// "Neue Verbindung" and the section "KI-Assistent verbinden". Layout, copy and
// states follow the approved design (docs/lacite/design/*.dc.html), built
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

// ── One connection card ─────────────────────────────────────────────────────

const LaCiteCard = {
    components: { LaCiteCopy },
    props: { conn: { type: Object, required: true }, now: { type: Number, default: 0 } },
    emits: ['edit', 'source', 'changed', 'removed', 'toast'],
    data() { return { confirming: false, confirmOverwrite: false, syncing: false, error: '' }; },
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
        errorText() { return laciteErrorText({ code: this.st.error }); },
    },
    methods: {
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
  <div v-if="missing && !paused" class="lacite-callout is-warn lacite-small-callout">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/></svg>
    <span>{{ missing.head }}<template v-for="(k, i) in missing.keys" :key="k"><span class="lb-mono">{{ k }}</span><template v-if="i < missing.keys.length - 1">, </template></template><template v-if="missing.rest"> und {{ missing.rest }} weitere</template> – Tippfehler oder noch nicht importiert?</span>
  </div>
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
};

LB.registerPlugin({
    id: 'lacite',
    views: { 'lacite.home': LaCiteHome },
});
})();
