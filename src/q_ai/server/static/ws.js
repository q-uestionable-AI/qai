// ws.js — WebSocket event dispatcher for the operations page
(function () {
    var root = document.getElementById('operations-root');
    if (!root) return;

    var runId = root.dataset.runId || null;
    var wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
    var socket;
    var elapsedInterval = null;

    var TERMINAL_STATUSES = [2, 3, 4, 6]; // COMPLETED, FAILED, CANCELLED, PARTIAL

    var STATUS_LABELS = {
        0: 'PENDING', 1: 'RUNNING', 2: 'COMPLETED',
        3: 'FAILED', 4: 'CANCELLED', 5: 'WAITING_FOR_USER', 6: 'PARTIAL',
    };
    var STATUS_CLASSES = {
        0: 'badge-ghost', 1: 'badge-info', 2: 'badge-success',
        3: 'badge-error', 4: 'badge-ghost', 5: 'badge-warning', 6: 'badge-warning',
    };

    // --- Status bar data attribute access ---

    function getStatusBar() {
        return document.getElementById('operations-status-bar');
    }

    // --- Elapsed timer ---

    function formatElapsed(totalSeconds) {
        var s = Math.max(0, Math.floor(totalSeconds));
        var h = Math.floor(s / 3600);
        var m = Math.floor((s % 3600) / 60);
        var sec = s % 60;
        var mm = (m < 10 ? '0' : '') + m;
        var ss = (sec < 10 ? '0' : '') + sec;
        if (h > 0) {
            return h + ':' + mm + ':' + ss;
        }
        return mm + ':' + ss;
    }

    function parseISOtoEpoch(isoStr) {
        if (!isoStr) return null;
        // Ensure trailing Z for UTC if no timezone info present
        var s = isoStr;
        if (!/[Zz+\-]/.test(s.slice(-6))) {
            s += 'Z';
        }
        var ms = Date.parse(s);
        return isNaN(ms) ? null : ms;
    }

    function startElapsedTimer(startedAtISO) {
        stopElapsedTimer();
        var startMs = parseISOtoEpoch(startedAtISO);
        if (!startMs) return;
        var el = document.getElementById('workflow-elapsed');
        if (!el) return;

        function tick() {
            var now = Date.now();
            el.textContent = formatElapsed((now - startMs) / 1000);
        }
        tick();
        elapsedInterval = setInterval(tick, 1000);
    }

    function stopElapsedTimer() {
        if (elapsedInterval !== null) {
            clearInterval(elapsedInterval);
            elapsedInterval = null;
        }
    }

    function showFinalElapsed(startedAtISO, finishedAtISO) {
        stopElapsedTimer();
        var startMs = parseISOtoEpoch(startedAtISO);
        var endMs = parseISOtoEpoch(finishedAtISO);
        var el = document.getElementById('workflow-elapsed');
        if (!el) return;
        if (startMs && endMs) {
            el.textContent = formatElapsed((endMs - startMs) / 1000);
        } else if (startMs) {
            // Terminal but no finished_at — show elapsed to now
            el.textContent = formatElapsed((Date.now() - startMs) / 1000);
        }
    }

    function initElapsedTimer() {
        var bar = getStatusBar();
        if (!bar) return;
        var status = parseInt(bar.dataset.workflowStatus, 10);
        var startedAt = bar.dataset.startedAt || '';
        var finishedAt = bar.dataset.finishedAt || '';

        if (isNaN(status) || !startedAt) return; // No run or no start time

        if (status === 1 || status === 5) {
            // RUNNING or WAITING_FOR_USER — live timer
            startElapsedTimer(startedAt);
        } else if (TERMINAL_STATUSES.indexOf(status) !== -1) {
            // Terminal — show final duration
            showFinalElapsed(startedAt, finishedAt);
        }
    }

    // --- WebSocket handlers ---

    function connect() {
        socket = new WebSocket(wsUrl);

        socket.onopen = function () {
            // Re-fetch status bar to catch terminal events that fired before
            // the WebSocket connected (race between page load and run completion).
            if (runId) {
                htmx.ajax('GET', '/api/operations/workflow-status-bar?run_id=' + runId,
                          {target: '#operations-status-bar', swap: 'outerHTML'}).then(function () {
                    initElapsedTimer();
                });
            }
        };

        socket.onmessage = function (e) {
            var event = JSON.parse(e.data);
            dispatch(event);
        };

        socket.onclose = function () {
            // Passive disconnect — no auto-reconnect for MVP.
        };
    }

    function dispatch(event) {
        switch (event.type) {
            case 'run_status':   handleRunStatus(event);  break;
            case 'progress':     handleProgress(event);   break;
            case 'finding':      handleFinding(event);    break;
            case 'waiting':      handleWaiting(event);    break;
            case 'resumed':      handleResumed(event);    break;
        }
    }

    function handleRunStatus(event) {
        if (event.run_id === runId && TERMINAL_STATUSES.indexOf(event.status) !== -1) {
            // Terminal parent status — re-fetch full status bar partial so
            // badge, elapsed, and report link all render from fresh DB state
            stopElapsedTimer();
            htmx.ajax('GET', '/api/operations/workflow-status-bar?run_id=' + runId,
                      {target: '#operations-status-bar', swap: 'outerHTML'}).then(function () {
                initElapsedTimer();
            });
        } else if (event.run_id === runId) {
            // Non-terminal parent update — just update the badge inline
            var badge = document.getElementById('workflow-status-badge');
            if (badge) {
                badge.textContent = STATUS_LABELS[event.status] || event.status;
                badge.className = 'badge badge-sm ' + (STATUS_CLASSES[event.status] || 'badge-ghost');
            }
        }
        // Any run_status → refresh child badges via HTMX
        if (runId) {
            htmx.ajax('GET', '/api/operations/status-bar?run_id=' + runId,
                      {target: '#child-run-badges', swap: 'innerHTML'});
        }
    }

    function handleProgress(event) {
        if (!runId || event.run_id !== runId) return;
        var el = document.getElementById('workflow-progress');
        if (el) el.textContent = event.message;
    }

    function handleFinding(event) {
        if (runId) {
            htmx.ajax('GET', '/api/operations/findings-sidebar?run_id=' + runId,
                      {target: '#findings-sidebar-content', swap: 'innerHTML'});
        }
    }

    function handleWaiting(event) {
        if (event.run_id !== runId) return;
        var banner = document.getElementById('waiting-banner');
        var msg = document.getElementById('waiting-message');
        var btn = document.getElementById('resume-btn');
        if (banner) banner.classList.remove('hidden');
        if (msg) msg.textContent = event.message;
        if (btn) btn.classList.remove('hidden');
    }

    function handleResumed(event) {
        if (event.run_id !== runId) return;
        var banner = document.getElementById('waiting-banner');
        if (banner) banner.classList.add('hidden');
    }

    // Expose for the Resume button onclick
    window.resumeWorkflow = function () {
        if (!runId) return;
        fetch('/api/workflows/' + runId + '/resume', {method: 'POST'});
        var btn = document.getElementById('resume-btn');
        if (btn) btn.classList.add('hidden');
    };

    // --- Findings sidebar navigation ---
    window.switchToFinding = function (module, findingId) {
        // 1. Switch to the correct module tab
        var tabBtn = document.querySelector('[role="tab"][onclick*="switchTab(this, \'' + module + '\')"]');
        if (tabBtn) {
            tabBtn.click();
        }
        // 2. Wait for tab to render, then scroll + highlight
        setTimeout(function () {
            var row = document.getElementById('finding-' + findingId);
            if (row) {
                row.scrollIntoView({ behavior: 'smooth', block: 'center' });
                row.classList.remove('finding-highlight');
                // Force reflow to restart animation
                void row.offsetWidth;
                row.classList.add('finding-highlight');
            }
        }, 100);
    };

    initElapsedTimer();
    connect();
})();
