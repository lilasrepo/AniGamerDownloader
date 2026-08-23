// AniGamerDownloader Dashboard — single-page app (framework-free vanilla ES module).
// Merges the settings logic (old aniGamerPlus.js) + task polling (old monitor.js)
// + top-tab switching. Four tabs: 儀表板 / 片單 / 資料庫 / 設定 (使用說明 is a
// dialog off the top bar). The task poll is gated on the Dashboard tab being
// active AND the document being visible.

// id_list comes from settings_id_list.js (classic <script> loaded before this module).
const id_list = window.id_list;

let dataArrays; // user config json

// ---- helpers ----------------------------------------------------------------

const byId = (id) => document.getElementById(id);

function showUploadStatus(ok) {
	byId('uploadOk').style.display = ok ? '' : 'none';
	byId('uploadFailed').style.display = ok ? 'none' : '';
	byId('uploadStatus').showModal();
}

// Transient bottom-right notice. Used instead of alert() for outcomes the user
// does not need to acknowledge — an alert costs a second click for information
// that is already visible in the UI a moment later.
let toastTimer = null;
function showToast(message, kind) {
	const el = byId('toast');
	if (!el) return;
	el.textContent = message;
	el.className = 'toast is-shown' + (kind ? ' is-' + kind : '');
	clearTimeout(toastTimer);
	toastTimer = setTimeout(() => { el.className = 'toast'; }, kind === 'error' ? 6000 : 3500);
}

// ============================================================================
// SETTINGS (from aniGamerPlus.js — behaviour preserved exactly)
// ============================================================================

// ---- config load / render ---------------------------------------------------

async function loadConfig() {
	const resp = await fetch('data/config.json');
	dataArrays = await resp.json();
	renderJson();
}

function renderJson() {
	for (const id of id_list) {
		const el = byId(id);
		switch (el.type) {
			case 'text':
			case 'number':
			case 'password':
				if (id === 'multi-thread') { // default thread count for manual tasks
					byId('manual_thread_limit').value = dataArrays[id];
				}
				el.value = dataArrays[id];
				break;
			case 'checkbox':
				el.checked = dataArrays[id];
				break;
			case 'select-one':
				el.value = dataArrays[id];
				break;
		}
	}
	renderGroupSummaries();
}

// One-line recaps shown on the COLLAPSED settings groups, so a closed group
// still tells you what it currently holds. Derived purely from dataArrays —
// nothing here is displayed that the config does not actually say.
function renderGroupSummaries() {
	const set = (id, text) => { const el = byId(id); if (el) el.textContent = text; };

	set('sum_paths',
		(dataArrays.bangumi_dir || 'data/bangumi')
		+ '　・　暫存 ' + (dataArrays.temp_dir || 'data/temp'));

	const nameBits = [];
	if (dataArrays.add_bangumi_name_to_video_filename) nameBits.push('含番劇名');
	if (dataArrays.add_resolution_to_video_filename) nameBits.push('含解析度');
	if (dataArrays.customized_video_filename_prefix) nameBits.push('前綴 ' + dataArrays.customized_video_filename_prefix);
	if (dataArrays.customized_video_filename_suffix) nameBits.push('後綴 ' + dataArrays.customized_video_filename_suffix);
	set('sum_filename', nameBits.length ? nameBits.join(' ・ ') : '僅集數，無附加');

	const sysBits = [];
	sysBits.push(dataArrays.save_logs ? '保留 ' + dataArrays.quantity_of_logs + ' 份日誌' : '不記錄日誌');
	sysBits.push(dataArrays.check_latest_version ? '啟動時檢查更新' : '不檢查更新');
	set('sum_system', sysBits.join(' ・ '));
}

async function reloadSetting() {
	await loadConfig();
}

// ---- config save ------------------------------------------------------------

async function readSettings() {
	for (const id of id_list) {
		const el = byId(id);
		switch (el.type) {
			case 'number':
				dataArrays[id] = Number(el.value);
				break;
			case 'text':
			case 'password':
				dataArrays[id] = el.value;
				break;
			case 'checkbox':
				dataArrays[id] = el.checked;
				break;
			case 'select-one':
				dataArrays[id] = el.value;
				break;
		}
	}

	try {
		const resp = await fetch('/uploadConfig', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json; charset=utf-8' },
			body: JSON.stringify(dataArrays),
		});
		if (!resp.ok) throw new Error('upload failed');
		showUploadStatus(true);
		await reloadSetting();
	} catch (e) {
		showUploadStatus(false);
	}
}

// ---- UA ---------------------------------------------------------------------

function getUA() {
	byId('ua').value = navigator.userAgent;
	alert('已取得當前瀏覽器UA');
}

// ---- manual task ------------------------------------------------------------

async function readManualConfig() {
	const link = byId('manual_link').value;
	if (link.length === 0) {
		alert('請輸入影片連結！');
		return;
	}

	const sn = link.replace(/(https:\/\/)?ani\.gamer\.com\.tw\/animeVideo\.php\?sn=/i, '').trim();
	if (!/^\d+$/.test(sn)) {
		showToast('看不出 sn：請貼上播放頁網址，或直接填 sn 數字。', 'error');
		return;
	}

	// Staged, not started — same as every other download path now.
	await doEnqueue([{
		kind: 'manual',
		sn: Number(sn),
		title: '單次任務 sn ' + sn + '（' + byId('manual_mode').value + '）',
		options: {
			mode: byId('manual_mode').value,
			resolution: byId('manual_resolution').value,
			classify: byId('manual_classify').checked,
			thread: byId('manual_thread_limit').value,
			danmu: byId('manual_danmu').checked,
		},
	}]);
}

// ---- sn_list ----------------------------------------------------------------

async function postSnList() {
	try {
		const resp = await fetch('/sn_list', {
			method: 'POST',
			headers: { 'Content-Type': 'text/plain; charset=utf-8' },
			body: byId('sn_list').value,
		});
		if (!resp.ok) throw new Error('sn_list failed');
		showUploadStatus(true);
		await showSnList();
	} catch (e) {
		showUploadStatus(false);
	}
}

async function showSnList() {
	const resp = await fetch('data/sn_list');
	byId('sn_list').value = await resp.text();
}

// ---- cookie -----------------------------------------------------------------
// Cookie editor mirrors the sn_list pattern: GET /data/cookie loads the current
// (masked) value, POST /cookie writes the raw header text. The matching UA field
// is the same config `ua` key — loaded from dataArrays and saved via the existing
// /uploadConfig path so cookie + UA stay consistent without a new endpoint.

async function showCookie() {
	const resp = await fetch('data/cookie');
	const data = await resp.json();
	byId('cookie_masked').value = data.masked || '（尚未設定）';
	byId('cookie_text').value = '';
	const sum = byId('sum_cookie');
	if (sum) sum.textContent = data.masked ? '已設定 cookie' : '尚未設定 cookie';
}

async function postCookie() {
	try {
		// write the cookie text (mirrors sn_list: text/plain body to /cookie)
		const resp = await fetch('/cookie', {
			method: 'POST',
			headers: { 'Content-Type': 'text/plain; charset=utf-8' },
			body: byId('cookie_text').value,
		});
		if (!resp.ok) throw new Error('cookie failed');

		// also write the UA back to config (kept consistent with the browser that produced the cookie), reusing the existing /uploadConfig path
		if (dataArrays) {
			dataArrays.ua = byId('ua').value;
			const cfgResp = await fetch('/uploadConfig', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json; charset=utf-8' },
				body: JSON.stringify(dataArrays),
			});
			if (!cfgResp.ok) throw new Error('ua upload failed');
		}

		showUploadStatus(true);
		await loadConfig();   // sync the ua field on the Settings page
		await showCookie();   // reload the masked value
	} catch (e) {
		showUploadStatus(false);
	}
}

// ============================================================================
// MONITOR (from monitor.js — polling, card add/update/remove, progress bars).
// Polling is gated on BOTH the Monitor tab being active AND the document visible.
// ============================================================================

const POLL_URL = 'data/tasks_progress_json';
const LOG_URL = 'data/log_tail?lines=100';
const POLL_INTERVAL = 1000;
let lastLogText = '';      // last rendered log text; skip DOM churn when unchanged

let monitorActive = false; // true only while the Monitor tab is the active tab
let timer = null;          // setTimeout handle for the next poll
let controller = null;     // AbortController for the in-flight fetch
let inFlight = false;      // guards against overlapping requests

// Polling is allowed only when the monitor tab is active and the page is visible.
function canPoll() {
	return monitorActive && !document.hidden;
}

// Build a task card. `waiting` cards are scheduled-but-not-started: same row,
// no progress bar (there is no progress to show yet).
function createCard(sn, info, waiting) {
	const card = document.createElement('div');
	card.className = 'task-card' + (waiting ? ' is-waiting' : '');
	card.id = 'task' + sn;
	card.dataset.mode = waiting ? 'waiting' : 'active';

	const header = document.createElement('div');
	header.className = 'task-card-header';
	header.id = 'header' + sn;
	header.textContent = waiting ? waitingTitle(sn, info) : info.filename;

	const body = document.createElement('div');
	body.className = 'task-card-body';

	const status = document.createElement('div');
	status.className = 'task-status';
	status.id = 'status' + sn;
	status.textContent = waiting ? waitingStatus(info) : info.status;

	body.appendChild(status);

	if (!waiting) {
		const progress = document.createElement('div');
		progress.className = 'progress';

		const bar = document.createElement('div');
		bar.className = 'progress-bar';
		bar.id = 'bar' + sn;

		const text = document.createElement('span');
		text.className = 'progress-text';
		text.id = 'ptext' + sn;

		progress.appendChild(bar);
		progress.appendChild(text);
		body.appendChild(progress);
		setProgress(bar, text, info.rate);
	}

	card.appendChild(header);
	card.appendChild(body);
	return card;
}

// Pending entries carry {name, episode} rather than a filename.
function waitingTitle(sn, info) {
	const title = info.name ? '《' + info.name + '》' : 'sn=' + sn;
	return title + (info.episode ? ' 第 ' + info.episode + ' 集' : '');
}

function waitingStatus(info) {
	// queue-dispatched items carry their own reason (they may be parsing, not
	// just queued); for the daemon's own scheduled tasks the concurrency cap IS
	// the reason, so name it when we know it
	if (info && info.status) return info.status;
	const limit = dataArrays && dataArrays['multi-thread'];
	return limit ? '等待中（併發上限 ' + limit + '）' : '等待中';
}

function setProgress(bar, text, rate) {
	const pct = Math.round(Number(rate) || 0);
	bar.style.width = pct + '%';
	text.textContent = pct + '%';
}

// Poll payload is { active: {sn:{rate,filename,status}}, pending: {sn:{name,episode}} }.
function renderTasks(payload) {
	payload = payload && typeof payload === 'object' ? payload : {};
	renderWorkList(payload.active || {}, payload.pending || {});
	renderQueue(payload.queue || []);
}

// ---- manual download queue ---------------------------------------------------
// Items staged by the operator (資料庫 / 片單) that have NOT started. Distinct
// from `pending`, which the daemon already scheduled and which starts on its own
// as soon as a concurrency slot frees up.

let queueSignature = '';   // skip the DOM rebuild while the queue is unchanged

function renderQueue(items) {
	const section = byId('queue_section');
	if (!section) return;
	items = Array.isArray(items) ? items : [];

	byId('stat_queue').textContent = String(items.length);
	byId('queue_count').textContent = items.length + ' 項';
	section.style.display = items.length === 0 ? 'none' : '';

	// path is part of the signature: re-adding an item keeps its id but can change
	// where it lands, and a stale path on screen would be worse than a rebuild.
	const signature = items.map((i) => i.id + '@' + (i.path || '')).join('|');
	if (signature === queueSignature) return;
	queueSignature = signature;

	const list = byId('queue_list');
	list.textContent = '';
	items.forEach((item) => {
		const row = document.createElement('div');
		row.className = 'queue-row';

		const main = document.createElement('div');
		main.className = 'queue-main';

		const name = document.createElement('span');
		name.className = 'queue-name';
		name.textContent = item.title;
		main.appendChild(name);

		if (item.path) {
			const path = document.createElement('span');
			path.className = 'queue-path';
			path.title = item.path;
			path.textContent = item.path;
			// only a db row that already finished once knows the real filename;
			// everything else can only promise the destination folder
			if (!item.path_exact) {
				const hint = document.createElement('span');
				hint.className = 'queue-path-hint';
				hint.textContent = '（資料夾；檔名於下載時決定）';
				path.appendChild(hint);
			}
			main.appendChild(path);
		}

		const meta = document.createElement('span');
		meta.className = 'queue-meta';
		meta.textContent = 'sn ' + item.sn + ' · ' + item.added;

		const acts = document.createElement('span');
		acts.className = 'queue-row-actions';
		acts.appendChild(makeBtn('開始', 'btn-primary btn-sm', () => startQueue([item.id])));
		acts.appendChild(makeBtn('移除', 'btn-danger btn-sm', () => removeQueue([item.id])));

		row.appendChild(main);
		row.appendChild(meta);
		row.appendChild(acts);
		list.appendChild(row);
	});
}

async function startQueue(ids) {
	try {
		const r = await postJson('/queue/start', { ids: ids || [] });
		renderQueue(r.items);
		if (r.started) showToast('已開始下載 ' + r.started + ' 項。');
	} catch (e) {
		showToast('開始下載失敗：' + e.message, 'error');
	}
}

async function removeQueue(ids) {
	try {
		const r = await postJson('/queue/remove', { ids });
		renderQueue(r.items);
	} catch (e) {
		showToast('移除失敗：' + e.message, 'error');
	}
}

async function clearQueue() {
	const n = Number(byId('stat_queue').textContent) || 0;
	if (n > 1 && !confirm('確定清空下載佇列（' + n + ' 項）？不會影響已經在下載的任務。')) return;
	try {
		const r = await postJson('/queue/clear', {});
		renderQueue(r.items);
		showToast('已清空下載佇列。');
	} catch (e) {
		showToast('清空失敗：' + e.message, 'error');
	}
}

// The queue also changes from other tabs, so refresh it on demand (the 1s poll
// only runs while the dashboard is open).
async function refreshQueue() {
	try {
		const resp = await fetch('data/queue');
		if (!resp.ok) return;
		const data = await resp.json();
		renderQueue(data.items || []);
	} catch (e) { /* dashboard poll will catch up */ }
}

// Live log: { file, exists, lines: [...] } from data/log_tail. Re-render only on
// change so we don't clobber the user's text selection / scroll position each second.
function renderLog(payload) {
	const view = byId('log_view');
	if (!view) return;
	const lines = (payload && payload.lines) || [];
	const text = lines.length
		? lines.join('\n')
		: (payload && payload.exists ? '（今日日誌為空）' : '（今日尚無日誌檔，下載開始後才會出現）');
	byId('log_file').textContent = payload && payload.file ? '(' + payload.file + ')' : '';
	if (text === lastLogText) return;
	lastLogText = text;
	const auto = byId('log_autoscroll');
	const prevTop = view.scrollTop;
	view.textContent = text;
	// stick to the bottom when auto-scroll is on; otherwise keep the user where they were
	view.scrollTop = (!auto || auto.checked) ? view.scrollHeight : prevTop;
}

// Pending list: scheduled tasks still waiting for a concurrency slot (not yet downloading, no progress bar).
// ONE list for everything in flight: downloading tasks first (with a progress
// bar), then scheduled-but-waiting ones. They used to be two separate sections,
// which read as two unrelated queues when they are really the same pipeline —
// only 佇列 (staged by the operator, needs a click) is genuinely different.
// The server dual-writes so active ∩ pending = ∅; the filter below keeps a
// transient overlap from ever rendering the same sn twice.
function renderWorkList(active, pending) {
	const panel = byId('task_info_panel');
	active = active && typeof active === 'object' ? active : {};
	pending = pending && typeof pending === 'object' ? pending : {};

	const activeSns = Object.keys(active);
	const pendingSns = Object.keys(pending).filter((sn) => !(sn in active));

	byId('stat_active').textContent = String(activeSns.length);
	byId('stat_pending').textContent = String(pendingSns.length);
	byId('no_task').style.display = (activeSns.length + pendingSns.length) === 0 ? '' : 'none';

	const wanted = [];
	activeSns.forEach((sn) => wanted.push({ sn, info: active[sn], waiting: false }));
	pendingSns.forEach((sn) => wanted.push({ sn, info: pending[sn] || {}, waiting: true }));

	wanted.forEach(function (item) {
		const mode = item.waiting ? 'waiting' : 'active';
		let card = byId('task' + item.sn);
		// a card whose mode changed (waiting -> downloading) is rebuilt, since a
		// waiting card has no progress bar to update
		if (card && card.dataset.mode !== mode) {
			card.remove();
			card = null;
		}
		if (card) {
			if (item.waiting) {
				byId('header' + item.sn).textContent = waitingTitle(item.sn, item.info);
				byId('status' + item.sn).textContent = waitingStatus(item.info);
			} else {
				byId('header' + item.sn).textContent = item.info.filename;
				byId('status' + item.sn).textContent = item.info.status;
				setProgress(byId('bar' + item.sn), byId('ptext' + item.sn), item.info.rate);
			}
		} else {
			card = createCard(item.sn, item.info, item.waiting);
		}
		// appendChild moves an existing node, so this also fixes the order
		// (downloading above waiting) without recreating anything
		panel.appendChild(card);
	});

	// drop cards for tasks that finished and left both dicts
	const keep = new Set(wanted.map((i) => 'task' + i.sn));
	Array.prototype.slice.call(panel.children).forEach(function (child) {
		if (!keep.has(child.id)) child.remove();
	});
}

function scheduleNext() {
	// only re-arm when polling is currently allowed
	if (timer === null && canPoll()) {
		timer = setTimeout(poll, POLL_INTERVAL);
	}
}

function poll() {
	timer = null;
	if (inFlight || !canPoll()) {
		return;
	}
	inFlight = true;
	controller = new AbortController();
	const opts = { signal: controller.signal };

	Promise.all([
		fetch(POLL_URL, opts).then(function (resp) {
			if (!resp.ok) {
				throw new Error('HTTP ' + resp.status);
			}
			return resp.json();
		}),
		// log is best-effort: a failure here must not break progress polling
		fetch(LOG_URL, opts).then(function (resp) {
			return resp.ok ? resp.json() : null;
		}).catch(function () {
			return null;
		}),
	])
		.then(function (results) {
			renderTasks(results[0]);
			if (results[1]) renderLog(results[1]);
		})
		.catch(function () {
			// swallow network / abort errors; the next poll will retry
		})
		.finally(function () {
			inFlight = false;
			controller = null;
			scheduleNext();
		});
}

function stopPolling() {
	if (timer !== null) {
		clearTimeout(timer);
		timer = null;
	}
	if (controller) {
		controller.abort();
	}
}

// Start polling immediately (used when the monitor tab becomes active+visible).
function startPolling() {
	if (canPoll()) {
		poll();
	}
}

// Pause/resume polling when the tab visibility changes.
document.addEventListener('visibilitychange', function () {
	if (document.hidden) {
		stopPolling();
	} else {
		startPolling();
	}
});

// ============================================================================
// DB INVENTORY (Feature 1) + BATCH PAUSE (Feature 2)
// ============================================================================

let inventoryData = []; // last loaded inventory (array of {anime_name, episodes, counts})

const STATE_META = {
	ok:             { label: '完整',     cls: 'st-ok' },
	missing:        { label: '缺檔',     cls: 'st-missing' },
	suspect:        { label: '大小可疑', cls: 'st-suspect' },
	not_downloaded: { label: '尚未下載', cls: 'st-none' },
	skipped:        { label: '已跳過',   cls: 'st-none' },
	corrupt:        { label: '損毀',     cls: 'st-missing' },
	verifying:      { label: '驗證中…',  cls: 'st-suspect' },
	no_ffprobe:     { label: '無ffprobe', cls: 'st-none' },
};

function setBadge(el, state) {
	const meta = STATE_META[state] || STATE_META.not_downloaded;
	el.textContent = meta.label;
	el.className = 'badge ' + meta.cls;
}

function makeBtn(label, cls, onClick) {
	const b = document.createElement('button');
	b.type = 'button';
	b.className = 'btn ' + cls;
	b.textContent = label;
	b.addEventListener('click', onClick);
	return b;
}

async function postJson(url, body) {
	const resp = await fetch(url, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json; charset=utf-8' },
		body: JSON.stringify(body),
	});
	if (!resp.ok) throw new Error('HTTP ' + resp.status);
	return resp.json();
}

async function loadInventory() {
	const container = byId('inventory_list');
	container.textContent = '載入中…';
	try {
		const resp = await fetch('/data/db_inventory');
		if (!resp.ok) throw new Error('HTTP ' + resp.status);
		inventoryData = await resp.json();
		renderInventory();
	} catch (e) {
		container.textContent = '載入失敗：' + e.message;
	}
}

function makeRow(ep, animeName) {
	const tr = document.createElement('tr');

	const tdEp = document.createElement('td');
	tdEp.textContent = '第 ' + ep.episode + ' 集';

	const tdRes = document.createElement('td');
	tdRes.textContent = ep.resolution ? ep.resolution + 'P' : '—';

	const tdSize = document.createElement('td');
	tdSize.className = 'inv-size';
	if (ep.exists) {
		tdSize.textContent = ep.actual_size + ' / ' + ep.db_size + ' MB';
	} else {
		tdSize.textContent = ep.db_size ? '— / ' + ep.db_size + ' MB' : '—';
	}

	const tdState = document.createElement('td');
	const badge = document.createElement('span');
	badge.id = 'inv_badge_' + ep.sn;
	setBadge(badge, ep.state);
	tdState.appendChild(badge);

	const tdAct = document.createElement('td');
	tdAct.className = 'inv-row-actions';
	tdAct.appendChild(makeBtn('加入佇列', 'btn-secondary btn-sm', () => doEnqueue([{
		kind: 'db', sn: ep.sn, title: animeName + ' 第 ' + ep.episode + ' 集',
		path: ep.path || '',   // empty -> the server fills in the destination folder
	}])));
	tdAct.appendChild(makeBtn('重置', 'btn-dark btn-sm', () => doReset([ep.sn])));
	tdAct.appendChild(makeBtn('移除紀錄', 'btn-danger btn-sm', () => doDelete([ep.sn], '第 ' + ep.episode + ' 集')));

	tr.appendChild(tdEp);
	tr.appendChild(tdRes);
	tr.appendChild(tdSize);
	tr.appendChild(tdState);
	tr.appendChild(tdAct);
	return tr;
}

function renderInventory() {
	const container = byId('inventory_list');
	container.textContent = '';

	if (!inventoryData.length) {
		container.textContent = '資料庫中沒有任何紀錄。';
		byId('inv_summary').textContent = '';
		return;
	}

	let totalEps = 0, totalOk = 0, totalMissing = 0, totalSuspect = 0, totalNone = 0, totalSkipped = 0;

	inventoryData.forEach((series) => {
		const c = series.counts;
		totalEps += series.episodes.length;
		totalOk += c.ok || 0;
		totalMissing += c.missing || 0;
		totalSuspect += c.suspect || 0;
		totalNone += c.not_downloaded || 0;
		totalSkipped += c.skipped || 0;

		const group = document.createElement('details');
		group.className = 'inv-series';
		if ((c.missing || 0) + (c.suspect || 0) > 0) group.open = true; // auto-open problem series

		const summary = document.createElement('summary');
		summary.className = 'inv-series-head';

		const name = document.createElement('span');
		name.className = 'inv-series-name';
		name.textContent = series.anime_name;

		const meta = document.createElement('span');
		meta.className = 'inv-series-meta';
		meta.textContent = series.episodes.length + ' 集 · 完整 ' + (c.ok || 0)
			+ (c.missing ? ' · 缺檔 ' + c.missing : '')
			+ (c.suspect ? ' · 可疑 ' + c.suspect : '');

		const actions = document.createElement('span');
		actions.className = 'inv-series-actions';
		const allSns = series.episodes.map((e) => e.sn);
		const existingSns = series.episodes.filter((e) => e.exists).map((e) => e.sn);
		if (existingSns.length) {
			actions.appendChild(makeBtn('整部驗證', 'btn-secondary btn-sm', (ev) => { ev.preventDefault(); doVerify(existingSns, ev.currentTarget); }));
		}
		actions.appendChild(makeBtn('整部重置', 'btn-dark btn-sm', (ev) => { ev.preventDefault(); doReset(allSns); }));
		actions.appendChild(makeBtn('整部加入佇列', 'btn-secondary btn-sm', (ev) => {
			ev.preventDefault();
			doEnqueue(series.episodes.map((e) => ({
				kind: 'db', sn: e.sn, title: series.anime_name + ' 第 ' + e.episode + ' 集',
				path: e.path || '',
			})));
		}));
		const missingSns = series.episodes.filter((e) => e.state === 'missing').map((e) => e.sn);
		if (missingSns.length) {
			actions.appendChild(makeBtn('移除缺檔 (' + missingSns.length + ')', 'btn-danger btn-sm', (ev) => { ev.preventDefault(); doDelete(missingSns, '《' + series.anime_name + '》的缺檔紀錄'); }));
		}
		actions.appendChild(makeBtn('整部移除', 'btn-danger btn-sm', (ev) => { ev.preventDefault(); doDelete(allSns, '《' + series.anime_name + '》全部 ' + allSns.length + ' 集'); }));

		summary.appendChild(name);
		summary.appendChild(meta);
		summary.appendChild(actions);
		group.appendChild(summary);

		const table = document.createElement('table');
		table.className = 'inv-table';
		const tbody = document.createElement('tbody');
		series.episodes.forEach((ep) => tbody.appendChild(makeRow(ep, series.anime_name)));
		table.appendChild(tbody);
		group.appendChild(table);
		container.appendChild(group);
	});

	byId('inv_summary').textContent = '共 ' + totalEps + ' 集 · 完整 ' + totalOk
		+ ' · 缺檔 ' + totalMissing + ' · 可疑 ' + totalSuspect + ' · 尚未下載 ' + totalNone
		+ (totalSkipped ? ' · 已跳過 ' + totalSkipped : '');
}

async function doReset(sns) {
	if (!sns.length) return;
	if (!confirm('確定重置這 ' + sns.length + ' 集的下載狀態？(不會刪除硬碟檔案)')) return;
	try {
		await postJson('/db_reset', { sns });
		await loadInventory();
	} catch (e) {
		alert('重置失敗：' + e.message);
	}
}

// Stage downloads instead of starting them. Nothing here touches the network:
// the items sit on the dashboard until the operator presses 開始. No confirm
// prompt — queueing is free and undoable (移除), so asking would be noise.
async function doEnqueue(entries) {
	if (!entries.length) return;
	try {
		const r = await postJson('/queue/add', { items: entries });
		renderQueue(r.items);
		const replaced = r.replaced ? '（其中 ' + r.replaced + ' 項已在佇列中，已更新）' : '';
		showToast('已加入下載佇列 ' + r.added + ' 項' + replaced + '，到「儀表板」按開始。');
	} catch (e) {
		showToast('加入佇列失敗：' + e.message, 'error');
	}
}

// Remove rows from the database. NEVER deletes files on disk.
// The server refuses with status 409 when any sn is still tracked by sn_list
// (the next scan would re-insert and re-download it); we then show exactly which
// ones and re-send with confirmed:true only if the user still wants it.
// `label` describes the target in the confirm prompt. Returns true if rows were deleted.
async function doDelete(sns, label) {
	if (!sns.length) return false;
	if (!confirm('確定從資料庫移除 ' + label + '（共 ' + sns.length + ' 筆紀錄）？\n\n只會刪除資料庫紀錄，硬碟上的檔案完全不會被動到。')) return false;
	try {
		let result = await postJson('/db_delete', { sns });
		if (result.status === 409) {
			if (!confirm(snListWarning(result) + '\n\n仍要移除嗎？')) return false;
			result = await postJson('/db_delete', { sns, confirmed: true });
		}
		await loadInventory();
		return (result.count || 0) > 0;
	} catch (e) {
		alert('移除失敗：' + e.message);
		return false;
	}
}

// Build the "these will come back" warning from a 409 / preview payload.
function snListWarning(info) {
	const lines = [];
	if (info.in_sn_list && info.in_sn_list.length) {
		lines.push('⚠ 其中 ' + info.in_sn_list.length + ' 筆仍在下載清單 (sn_list) 的追蹤範圍內。');
		lines.push('移除後，下一次自動檢查會重新加入並重新下載這些集數。');
		lines.push('若不想重抓，請先到「下載清單」分頁把該部動畫的那一行移除。');
	}
	if (info.unresolved_sn_list && info.unresolved_sn_list.length) {
		if (lines.length) lines.push('');
		lines.push('另有 ' + info.unresolved_sn_list.length + ' 個下載清單項目無法對應到資料庫紀錄 (sn: '
			+ info.unresolved_sn_list.join(', ') + ')，無法判斷是否會重抓，請自行確認。');
	}
	return lines.join('\n');
}

// Global two-step purge: preview first (deletes nothing), confirm second.
async function purgeMissing(btn) {
	btn = btn || byId('invPurgeBtn');
	const orig = btn.textContent;
	btn.disabled = true;
	btn.textContent = '掃描中…';
	let info;
	try {
		const resp = await fetch('/data/db_missing_preview');
		if (!resp.ok) throw new Error('HTTP ' + resp.status);
		info = await resp.json();
	} catch (e) {
		alert('掃描失敗：' + e.message);
		return;
	} finally {
		btn.disabled = false;
		btn.textContent = orig;
	}

	if (!info.total) { alert('沒有任何「缺檔」紀錄，不需要清理。'); return; }

	const lines = ['將從資料庫移除 ' + info.total + ' 筆「缺檔」紀錄（硬碟檔案不會被動到）：', ''];
	info.series.forEach((s) => lines.push('  ・《' + s.anime_name + '》 ' + s.count + ' 集'));
	const warn = snListWarning(info);
	if (warn) { lines.push(''); lines.push(warn); }
	lines.push('');
	lines.push('確定要移除嗎？');
	if (!confirm(lines.join('\n'))) return;

	try {
		const result = await postJson('/db_delete', { sns: info.sns, confirmed: true });
		await loadInventory();
		alert('已移除 ' + (result.count || 0) + ' 筆資料庫紀錄。');
	} catch (e) {
		alert('移除失敗：' + e.message);
	}
}

// sns: optional list to verify (per-series); when omitted, verify ALL files that exist.
// btn: optional triggering button to disable during the request (defaults to the global button).
async function doVerify(sns, btn) {
	if (!sns) {
		sns = [];
		inventoryData.forEach((s) => s.episodes.forEach((e) => { if (e.exists) sns.push(e.sn); }));
	}
	if (!sns.length) { alert('沒有可驗證的檔案 (硬碟上找不到任何已下載檔)。'); return; }
	btn = btn || byId('invVerifyBtn');
	const orig = btn.textContent;
	btn.disabled = true;
	btn.textContent = '驗證中…';
	sns.forEach((sn) => { const b = byId('inv_badge_' + sn); if (b) setBadge(b, 'verifying'); });
	try {
		const results = await postJson('/db_verify', { sns });
		Object.keys(results).forEach((sn) => {
			const b = byId('inv_badge_' + sn);
			if (b) setBadge(b, results[sn]);
		});
	} catch (e) {
		alert('驗證失敗：' + e.message);
		sns.forEach((sn) => { const b = byId('inv_badge_' + sn); if (b) setBadge(b, 'suspect'); });
	} finally {
		btn.disabled = false;
		btn.textContent = orig;
	}
}

// ---- batch pause/resume -----------------------------------------------------

let batchPaused = false;
let batchDaemon = false;
let batchReason = '';
let batchFailures = 0;
let batchLastFailure = '';

function applyBatchState() {
	const badge = byId('batch_state_badge');
	const btn = byId('batchToggleBtn');
	const checkBtn = byId('checkNowBtn');
	const shutdownBtn = byId('shutdownBtn');
	// whether shutdown is available is independent of the daemon (it stops the serving process itself)
	shutdownBtn.disabled = false;
	if (!batchDaemon) {
		badge.textContent = 'daemon 未運行';
		badge.className = 'badge st-none';
		btn.disabled = true;
		btn.textContent = '無法控制';
		btn.className = 'btn btn-secondary';
		checkBtn.disabled = true;
		return;
	}
	btn.disabled = false;
	checkBtn.disabled = false;
	if (batchPaused) {
		if (batchReason === 'startup') {
			badge.textContent = '待命中（尚未連線）';
			badge.title = '啟動時不主動連線。按「繼續」或「立即檢查」才會開始更新。';
		} else if (batchReason === 'failures') {
			badge.textContent = '已自動暫停（連續失敗 ' + batchFailures + '）';
			badge.title = '連續失敗過多，已停止繼續請求。'
				+ (batchLastFailure ? '最後一次失敗：' + batchLastFailure + ' ' : '')
				+ '建議先跑「網站健檢」與「帳號健檢」找出原因。';
		} else {
			badge.textContent = '已暫停';
			badge.title = '';
		}
		badge.className = 'badge st-suspect';
		btn.textContent = '繼續';
		btn.className = 'btn btn-primary';
	} else {
		badge.textContent = '運行中';
		badge.title = '';
		badge.className = 'badge st-ok';
		btn.textContent = '暫停';
		btn.className = 'btn btn-secondary';
	}
}

async function checkNow() {
	try {
		const r = await postJson('/daemon/check_now', {});
		alert('已觸發立即檢查，daemon 將馬上掃描一次 sn_list。'
			+ (r && r.resumed ? '\n（原本處於暫停狀態，已一併解除）' : ''));
		await refreshBatchStatus();
	} catch (e) {
		alert('觸發失敗：' + e.message);
	}
}

function formatDoctor(r) {
	const lines = [];
	lines.push('網站健檢 sn=' + (r.sn == null ? '(未指定)' : r.sn));
	if (r.error) lines.push('! ' + r.error);
	for (const s of (r.steps || [])) {
		lines.push((s.ok ? '✓ ' : '✗ ') + s.name + ' — ' + s.detail);
	}
	lines.push(r.ok ? '結果：正常，各步驟皆通過。' : '結果：異常。');
	if (!r.ok && r.first_broken) lines.push('中斷步驟代碼：' + r.first_broken + '（對照 docs/SITE_API.md 找對應端點）');
	if (!r.ok && r.diag) lines.push('原始回應：' + r.diag);
	return lines.join('\n');
}

async function runDoctor() {
	if (!confirm('網站健檢會對動畫瘋發出一次真實解析請求（等同一次手動查詢），確定執行？')) return;
	const btn = byId('doctorBtn');
	const out = byId('doctorResult');
	const prevText = btn.textContent;
	btn.disabled = true;
	btn.textContent = '健檢中…';
	out.style.display = 'block';
	out.textContent = '健檢中，請稍候…（若帳號非 VIP 需等待廣告時間，約數十秒）';
	try {
		const r = await postJson('/doctor', {});
		out.textContent = formatDoctor(r);
	} catch (e) {
		out.textContent = '健檢失敗：' + e.message;
	} finally {
		btn.disabled = false;
		btn.textContent = prevText;
	}
}

// ---- in-app incognito login (desktop window only) ---------------------------
//
// Only the pywebview desktop shell exposes window.pywebview.api (see
// shells/desktop.py LoginApi). In a plain browser it is absent, so the whole
// box stays hidden and the paste editor below remains the only path.
// pywebview injects the bridge asynchronously, hence the pywebviewready listener
// AND the immediate check — whichever happens first wins.

function hasPywebview() {
	return !!(window.pywebview && window.pywebview.api && window.pywebview.api.open_login);
}

function revealLoginBox() {
	const box = byId('login_window_box');
	if (box && hasPywebview()) box.hidden = false;
}

function setLoginStatus(text, kind) {
	const el = byId('login_status');
	if (!el) return;
	el.textContent = text || '';
	el.className = 'login-status' + (kind ? ' is-' + kind : '');
}

// Shared tail for both entry points: report, then resync everything the new
// cookie affects — masked value, passive badge, and a real server-side check.
async function afterLoginResult(result) {
	if (!result || !result.ok) {
		setLoginStatus((result && result.error) || '擷取失敗', 'fail');
		return;
	}
	setLoginStatus('已擷取 ' + result.keys.length + ' 個 cookie 並寫入，正在驗證帳號…', 'ok');
	await showCookie();
	await refreshAccountState();
	await accountCheckNow();
	setLoginStatus('已擷取 ' + result.keys.length + ' 個 cookie 並寫入。帳號狀態見「監控」分頁。', 'ok');
}

async function openLoginWindow() {
	const btn = byId('loginWindowBtn');
	const capBtn = byId('loginCaptureBtn');
	const orig = btn.textContent;
	btn.disabled = true;
	btn.textContent = '等待登入…';
	capBtn.disabled = false;
	setLoginStatus('已開啟登入視窗，請在該視窗完成登入（最多等待 5 分鐘）。', '');
	try {
		await afterLoginResult(await window.pywebview.api.open_login());
	} catch (e) {
		setLoginStatus('登入視窗發生錯誤：' + (e && e.message ? e.message : e), 'fail');
	} finally {
		btn.disabled = false;
		btn.textContent = orig;
		capBtn.disabled = true;
	}
}

async function captureLoginNow() {
	const capBtn = byId('loginCaptureBtn');
	capBtn.disabled = true;
	try {
		await afterLoginResult(await window.pywebview.api.capture_now());
	} catch (e) {
		setLoginStatus('擷取發生錯誤：' + (e && e.message ? e.message : e), 'fail');
	}
}

// ---- account / login state --------------------------------------------------

// Paint the status badge. `net` is the /account_check result when we have one;
// without it we only know what cookie.txt looks like on disk (no network).
function applyAccountState(passive, net) {
	const badge = byId('account_badge');
	const detail = byId('account_detail');
	if (net) {
		if (net.logged_in && net.vip) {
			badge.textContent = '已登入 · VIP';
			badge.className = 'badge st-ok';
		} else if (net.logged_in) {
			badge.textContent = '已登入 · 非 VIP';
			badge.className = 'badge st-suspect';
		} else {
			badge.textContent = '未登入';
			badge.className = 'badge st-missing';
		}
		detail.textContent = net.error ? (net.detail ? net.detail + '（' + net.error + '）' : net.error)
			: (net.detail || '');
		return;
	}
	if (!passive) { badge.textContent = '未知'; badge.className = 'badge'; detail.textContent = ''; return; }
	if (passive.logged_in) {
		badge.textContent = 'cookie 已載入';
		badge.className = 'badge st-ok';
		detail.textContent = (passive.mtime ? '更新於 ' + passive.mtime + ' · ' : '')
			+ '尚未向伺服器確認，按「帳號健檢」可查 VIP 狀態';
	} else {
		badge.textContent = '遊客 cookie';
		badge.className = 'badge st-missing';
		detail.textContent = '目前不是登入狀態，只能下載非 VIP 畫質。請到「設定」分頁更新 cookie。'
			+ (passive.has_invalid_backup ? '（舊 cookie 已被標記失效，存為 invalid_cookie.txt）' : '');
	}
}

async function refreshAccountState() {
	try {
		const resp = await fetch('/data/cookie_state');
		if (!resp.ok) throw new Error('HTTP ' + resp.status);
		applyAccountState(await resp.json(), null);
	} catch (e) {
		applyAccountState(null, null);
	}
}

async function runAccountCheck() {
	if (!confirm('帳號健檢會對動畫瘋發出一次真實請求（等同一次手動查詢），確定執行？')) return;
	await accountCheckNow();
}

// The check itself, without the confirmation prompt — the login flow calls this
// directly, since the user already opted in by logging in.
async function accountCheckNow() {
	const btn = byId('accountCheckBtn');
	const snRaw = byId('accountCheckSn').value.trim();
	const prevText = btn.textContent;
	btn.disabled = true;
	btn.textContent = '檢查中…';
	byId('account_badge').textContent = '檢查中…';
	byId('account_badge').className = 'badge';
	byId('account_detail').textContent = '';
	try {
		const r = await postJson('/account_check', snRaw ? { sn: snRaw } : {});
		applyAccountState(null, r);
	} catch (e) {
		byId('account_badge').textContent = '檢查失敗';
		byId('account_badge').className = 'badge st-missing';
		byId('account_detail').textContent = e.message;
	} finally {
		btn.disabled = false;
		btn.textContent = prevText;
	}
}

async function shutdownApp() {
	if (!confirm('確定要停止整個 AniGamerDownloader 程式？\n進行中的下載會中斷，需重新執行 aniGamer.bat（系統匣）才能再使用。')) return;
	try {
		await postJson('/shutdown', {});
	} catch (e) {
		// the server may shut down before responding; a dropped connection is expected
	}
	alert('已送出停止指令，程式即將關閉（控制臺將失去連線）。');
}

async function refreshBatchStatus() {
	try {
		const resp = await fetch('/batch/status');
		if (!resp.ok) throw new Error('HTTP ' + resp.status);
		const s = await resp.json();
		batchPaused = !!s.paused;
		batchDaemon = !!s.daemon;
		batchReason = s.reason || '';
		batchFailures = s.failures || 0;
		batchLastFailure = s.last_failure || '';
		applyBatchState();
	} catch (e) {
		// leave the control as-is on error
	}
}

async function toggleBatch() {
	try {
		await postJson(batchPaused ? '/batch/resume' : '/batch/pause', {});
		await refreshBatchStatus();
	} catch (e) {
		alert('切換失敗：' + e.message);
	}
}

// ============================================================================
// TOP-TAB SWITCHING
// ============================================================================

const TABS = ['dashboard', 'queue', 'library', 'settings'];
const DEFAULT_TAB = 'dashboard';

// Hashes from the previous six-tab layout, so old bookmarks still land somewhere
// sensible instead of silently falling back to the default tab.
const TAB_ALIASES = {
	monitor: 'dashboard',
	snlist: 'queue',
	manual: 'queue',
	inventory: 'library',
	help: 'dashboard',   // 使用說明 is now a dialog, not a tab
};

// Map a location (path "/monitor" or hash "#dashboard") onto a known tab name.
function tabFromLocation() {
	const hash = (location.hash || '').replace(/^#/, '');
	if (TABS.includes(hash)) return hash;
	if (TAB_ALIASES[hash]) return TAB_ALIASES[hash];
	if (/\/monitor\/?$/.test(location.pathname)) return 'dashboard';
	return DEFAULT_TAB;
}

// Render a tab (panels, buttons, monitor gating). Does NOT touch the URL —
// the hash is owned by the click handler / initial paint so that genuine tab
// switches create real history entries and back/forward works.
function activateTab(name) {
	const tab = TABS.includes(name) ? name : DEFAULT_TAB;

	for (const t of TABS) {
		const panel = byId('panel-' + t);
		const button = byId('tab-' + t);
		const active = t === tab;
		panel.classList.toggle('is-active', active);
		button.classList.toggle('is-active', active);
		button.setAttribute('aria-selected', active ? 'true' : 'false');
	}

	// gate the task poll on the Dashboard tab being active
	monitorActive = tab === 'dashboard';
	if (monitorActive) {
		refreshBatchStatus(); // sync the pause/resume button when entering Dashboard
		startPolling();
	} else {
		stopPolling();
	}

	// load the DB inventory when entering the Library tab (lazy; refreshable via button)
	if (tab === 'library') {
		loadInventory();
	}

}

// User-initiated tab switch: push a history entry by assigning location.hash
// (this also fires `hashchange`, which renders the tab). Guard against
// re-clicking the active tab so we don't stack duplicate history entries.
function selectTab(name) {
	const target = '#' + (TABS.includes(name) ? name : DEFAULT_TAB);
	if (location.hash === target) {
		// already here (e.g. re-click) — just re-render without a new entry
		activateTab(name);
	} else {
		location.hash = target; // pushes history + triggers hashchange -> activateTab
	}
}

// ============================================================================
// EVENT WIRING
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
	// settings actions
	byId('saveBtn').addEventListener('click', readSettings);
	byId('reloadBtn').addEventListener('click', reloadSetting);
	byId('getUaBtn').addEventListener('click', getUA);
	byId('manualSubmit').addEventListener('click', readManualConfig);
	byId('snListSubmit').addEventListener('click', postSnList);

	// cookie editor (account Cookie) actions
	byId('cookieSubmit').addEventListener('click', postCookie);
	byId('cookieReloadBtn').addEventListener('click', showCookie);
	byId('loginWindowBtn').addEventListener('click', openLoginWindow);
	byId('loginCaptureBtn').addEventListener('click', captureLoginNow);
	window.addEventListener('pywebviewready', revealLoginBox);
	revealLoginBox();  // in case the bridge landed before this listener

	// inventory (Database) + batch pause (Monitor) actions
	byId('invRefreshBtn').addEventListener('click', loadInventory);
	byId('invVerifyBtn').addEventListener('click', () => doVerify());
	byId('invPurgeBtn').addEventListener('click', (ev) => purgeMissing(ev.currentTarget));
	byId('batchToggleBtn').addEventListener('click', toggleBatch);
	byId('checkNowBtn').addEventListener('click', checkNow);
	byId('doctorBtn').addEventListener('click', runDoctor);
	byId('accountCheckBtn').addEventListener('click', runAccountCheck);
	byId('shutdownBtn').addEventListener('click', shutdownApp);

	// 使用說明 — a dialog off the top bar rather than its own tab
	byId('helpBtn').addEventListener('click', () => byId('helpDialog').showModal());

	// download queue (dashboard)
	byId('queueStartAllBtn').addEventListener('click', () => startQueue([]));
	byId('queueClearBtn').addEventListener('click', clearQueue);

	// tab buttons — selectTab assigns location.hash so back/forward works
	for (const t of TABS) {
		byId('tab-' + t).addEventListener('click', () => selectTab(t));
	}
	// back/forward navigation between hashes (also driven by selectTab's hash write)
	window.addEventListener('hashchange', () => activateTab(tabFromLocation()));

	// initial tab from path/hash; render without rewriting the URL on first paint
	activateTab(tabFromLocation());

	loadConfig();
	showSnList();
	showCookie();
	refreshAccountState();
	refreshQueue();   // the 1s poll only runs on the dashboard; seed it on any tab
});
