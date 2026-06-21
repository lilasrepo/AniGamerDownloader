// AniGamerDownloader Dashboard — single-page app (framework-free vanilla ES module).
// Merges the settings logic (old aniGamerPlus.js) + task monitor polling
// (old monitor.js) + top-tab switching. All previously-verified behaviour is
// preserved exactly; the only additions are tab navigation and gating the
// monitor poll on the Monitor tab being active AND the document being visible.

// id_list comes from settings_id_list.js (classic <script> loaded before this module).
const id_list = window.id_list;
id_list.push('proxy_protocol', 'proxy_ip', 'proxy_port', 'proxy_user', 'proxy_passwd');

let dataArrays; // user config json

// ---- helpers ----------------------------------------------------------------

const byId = (id) => document.getElementById(id);

function showUploadStatus(ok) {
	byId('uploadOk').style.display = ok ? '' : 'none';
	byId('uploadFailed').style.display = ok ? 'none' : '';
	byId('uploadStatus').showModal();
}

// ============================================================================
// SETTINGS (from aniGamerPlus.js — behaviour preserved exactly)
// ============================================================================

// ---- proxy parsing ----------------------------------------------------------

// Decompose the stored proxy string into protocol/ip/port/user/passwd and write
// them back onto dataArrays. Keeps the EXACT original regex logic; the protocol
// is kept LOWERCASE so a stored value round-trips unchanged.
function parseProxy(proxy) {
	let proxy_protocol = proxy.replace(/:\/\/.*/i, '');
	let proxy_ip;
	let proxy_port;
	let proxy_user = '';
	let proxy_passwd = '';

	if (/.*@.*/.test(proxy)) {
		proxy_user = /:\/\/.*?:/g.exec(proxy)[0].replace(/:(\/\/)?/g, '');
		proxy_passwd = /:.*@/.exec(proxy)[0].replace(proxy_user, '')
			.replace(/(:\/\/:)?@?/g, '');
		proxy = proxy.replace(proxy_user + ':' + proxy_passwd + '@', '');
	}

	if (proxy.length > 0) {
		proxy_ip = /:.*:/.exec(proxy)[0].replace(/:(\/\/)?/g, '');
		proxy_port = /:\d+/.exec(proxy)[0].replace(/:/, '');
	} else {
		proxy_ip = '';
		proxy_port = '';
	}

	dataArrays.proxy_protocol = proxy_protocol;
	dataArrays.proxy_ip = proxy_ip;
	dataArrays.proxy_port = proxy_port;
	dataArrays.proxy_user = proxy_user;
	dataArrays.proxy_passwd = proxy_passwd;
}

// ---- config load / render ---------------------------------------------------

async function loadConfig() {
	const resp = await fetch('data/config.json');
	dataArrays = await resp.json();
	parseProxy(dataArrays.proxy);
	renderJson();
}

function renderJson() {
	for (const id of id_list) {
		if (id === 'proxy') continue; // proxy settings have been decomposed
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
}

async function reloadSetting() {
	await loadConfig();
}

// ---- config save ------------------------------------------------------------

async function readSettings() {
	for (const id of id_list) {
		if (id === 'proxy') continue; // proxy settings have been decomposed
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

	// merge proxy config (compute once — the original did this redundantly inside the loop)
	const ip_port = dataArrays.proxy_ip + ':' + dataArrays.proxy_port;
	const protocol = dataArrays.proxy_protocol + '://';
	if ((dataArrays.proxy_user?.length * dataArrays.proxy_passwd?.length) === 0) {
		// if there is no username/password
		dataArrays.proxy = protocol + ip_port;
	} else {
		// if there is a username/password
		dataArrays.proxy = protocol + dataArrays.proxy_user + ':' + dataArrays.proxy_passwd + '@' + ip_port;
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

	const sn = link.replace(/(https:\/\/)?ani\.gamer\.com\.tw\/animeVideo\.php\?sn=/i, '');
	const manualData = {
		sn,
		mode: byId('manual_mode').value,
		resolution: byId('manual_resolution').value,
		classify: byId('manual_classify').checked,
		thread: byId('manual_thread_limit').value,
		danmu: byId('manual_danmu').checked,
	};

	try {
		const resp = await fetch('/manualTask', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json; charset=utf-8' },
			body: JSON.stringify(manualData),
		});
		if (!resp.ok) throw new Error('manual task failed');
		showUploadStatus(true);
		await reloadSetting();
	} catch (e) {
		showUploadStatus(false);
	}
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
const POLL_INTERVAL = 1000;

let monitorActive = false; // true only while the Monitor tab is the active tab
let timer = null;          // setTimeout handle for the next poll
let controller = null;     // AbortController for the in-flight fetch
let inFlight = false;      // guards against overlapping requests

// Polling is allowed only when the monitor tab is active and the page is visible.
function canPoll() {
	return monitorActive && !document.hidden;
}

// Build a task card from the contract markup. sn comes from the data keys.
function createCard(sn, info) {
	const card = document.createElement('div');
	card.className = 'task-card';
	card.id = 'task' + sn;

	const header = document.createElement('div');
	header.className = 'task-card-header';
	header.id = 'header' + sn;
	header.textContent = info.filename;

	const body = document.createElement('div');
	body.className = 'task-card-body';

	const status = document.createElement('div');
	status.className = 'task-status';
	status.id = 'status' + sn;
	status.textContent = info.status;

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
	body.appendChild(status);
	body.appendChild(progress);
	card.appendChild(header);
	card.appendChild(body);

	setProgress(bar, text, info.rate);
	return card;
}

function setProgress(bar, text, rate) {
	const pct = Math.round(Number(rate) || 0);
	bar.style.width = pct + '%';
	text.textContent = pct + '%';
}

// Poll payload is { active: {sn:{rate,filename,status}}, pending: {sn:{name,episode}} }.
function renderTasks(payload) {
	payload = payload && typeof payload === 'object' ? payload : {};
	renderActive(payload.active || {});
	renderPending(payload.pending || {});
}

// Pending list: scheduled tasks still waiting for a concurrency slot (not yet downloading, no progress bar).
function renderPending(data) {
	const section = byId('pending_section');
	const list = byId('pending_list');
	const sns = data && typeof data === 'object' ? Object.keys(data) : [];

	byId('pending_count').textContent = String(sns.length);
	section.style.display = sns.length === 0 ? 'none' : '';

	list.textContent = ''; // small list, changes rarely — simple rebuild each poll
	sns.forEach(function (sn) {
		const info = data[sn] || {};
		const row = document.createElement('div');
		row.className = 'pending-row';

		const name = document.createElement('span');
		name.className = 'pending-name';
		const title = info.name ? '《' + info.name + '》' : 'sn=' + sn;
		name.textContent = title + (info.episode ? ' 第 ' + info.episode + ' 集' : '');

		const tag = document.createElement('span');
		tag.className = 'badge st-suspect';
		tag.textContent = '等待中';

		row.appendChild(name);
		row.appendChild(tag);
		list.appendChild(row);
	});
}

function renderActive(data) {
	const noTask = byId('no_task');
	const panel = byId('task_info_panel');
	const sns = data && typeof data === 'object' ? Object.keys(data) : [];

	noTask.style.display = sns.length === 0 ? '' : 'none';

	// add or update a card per running task
	sns.forEach(function (sn) {
		const info = data[sn];
		const existing = byId('task' + sn);
		if (existing) {
			byId('status' + sn).textContent = info.status;
			byId('header' + sn).textContent = info.filename;
			setProgress(byId('bar' + sn), byId('ptext' + sn), info.rate);
		} else {
			// prepend so newest task shows on top (matches original behaviour)
			panel.insertBefore(createCard(sn, info), panel.firstChild);
		}
	});

	// remove cards whose task finished and was dropped from the progress dict
	Array.prototype.slice.call(panel.children).forEach(function (child) {
		const sn = (child.id || '').replace('task', '');
		if (sn && sns.indexOf(sn) === -1) {
			child.remove();
		}
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

	fetch(POLL_URL, { signal: controller.signal })
		.then(function (resp) {
			if (!resp.ok) {
				throw new Error('HTTP ' + resp.status);
			}
			return resp.json();
		})
		.then(renderTasks)
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

function makeRow(ep) {
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
	tdAct.appendChild(makeBtn('立即下載', 'btn-secondary btn-sm', () => doRedownload([ep.sn])));
	tdAct.appendChild(makeBtn('重置', 'btn-dark btn-sm', () => doReset([ep.sn])));

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

	let totalEps = 0, totalOk = 0, totalMissing = 0, totalSuspect = 0, totalNone = 0;

	inventoryData.forEach((series) => {
		const c = series.counts;
		totalEps += series.episodes.length;
		totalOk += c.ok || 0;
		totalMissing += c.missing || 0;
		totalSuspect += c.suspect || 0;
		totalNone += c.not_downloaded || 0;

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
		actions.appendChild(makeBtn('整部立即下載', 'btn-secondary btn-sm', (ev) => { ev.preventDefault(); doRedownload(allSns); }));

		summary.appendChild(name);
		summary.appendChild(meta);
		summary.appendChild(actions);
		group.appendChild(summary);

		const table = document.createElement('table');
		table.className = 'inv-table';
		const tbody = document.createElement('tbody');
		series.episodes.forEach((ep) => tbody.appendChild(makeRow(ep)));
		table.appendChild(tbody);
		group.appendChild(table);
		container.appendChild(group);
	});

	byId('inv_summary').textContent = '共 ' + totalEps + ' 集 · 完整 ' + totalOk
		+ ' · 缺檔 ' + totalMissing + ' · 可疑 ' + totalSuspect + ' · 尚未下載 ' + totalNone;
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

async function doRedownload(sns) {
	if (!sns.length) return;
	if (!confirm('確定立即重新下載這 ' + sns.length + ' 集？可到「監控」頁查看進度。')) return;
	try {
		await postJson('/db_redownload', { sns });
		alert('已開始下載 ' + sns.length + ' 集，請到「監控」頁查看進度。');
	} catch (e) {
		alert('立即下載失敗：' + e.message);
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
		badge.textContent = '已暫停';
		badge.className = 'badge st-suspect';
		btn.textContent = '繼續';
		btn.className = 'btn btn-primary';
	} else {
		badge.textContent = '運行中';
		badge.className = 'badge st-ok';
		btn.textContent = '暫停';
		btn.className = 'btn btn-secondary';
	}
}

async function checkNow() {
	try {
		await postJson('/daemon/check_now', {});
		alert('已觸發立即檢查，daemon 將馬上掃描一次 sn_list。');
	} catch (e) {
		alert('觸發失敗：' + e.message);
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
		applyBatchState();
	} catch (e) {
		// leave the control as-is on error
	}
}

async function toggleBatch() {
	try {
		const s = await postJson(batchPaused ? '/batch/resume' : '/batch/pause', {});
		batchPaused = !!s.paused;
		applyBatchState();
	} catch (e) {
		alert('切換失敗：' + e.message);
	}
}

// ============================================================================
// TOP-TAB SWITCHING
// ============================================================================

const TABS = ['settings', 'monitor', 'snlist', 'inventory', 'manual', 'help'];
const DEFAULT_TAB = 'settings';

// Map a location (path "/monitor" or hash "#monitor") onto a known tab name.
function tabFromLocation() {
	const hash = (location.hash || '').replace(/^#/, '');
	if (TABS.includes(hash)) return hash;
	if (/\/monitor\/?$/.test(location.pathname)) return 'monitor';
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

	// gate the monitor poll on the Monitor tab being active
	monitorActive = tab === 'monitor';
	if (monitorActive) {
		refreshBatchStatus(); // sync the pause/resume button when entering Monitor
		startPolling();
	} else {
		stopPolling();
	}

	// load the DB inventory when entering the Database tab (lazy; refreshable via button)
	if (tab === 'inventory') {
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

	// inventory (Database) + batch pause (Monitor) actions
	byId('invRefreshBtn').addEventListener('click', loadInventory);
	byId('invVerifyBtn').addEventListener('click', () => doVerify());
	byId('batchToggleBtn').addEventListener('click', toggleBatch);
	byId('checkNowBtn').addEventListener('click', checkNow);
	byId('shutdownBtn').addEventListener('click', shutdownApp);

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
});
