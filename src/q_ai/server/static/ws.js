// ws.js — WebSocket event dispatcher for the operations page
(function () {
    var root = document.getElementById('operations-root');
    if (!root) return;

    var runId = root.dataset.runId || null;
    var wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
    var socket;

    var STATUS_LABELS = {
        0: 'PENDING', 1: 'RUNNING', 2: 'COMPLETED',
        3: 'FAILED', 4: 'CANCELLED', 5: 'WAITING_FOR_USER', 6: 'PARTIAL',
    };
    var STATUS_CLASSES = {
        0: 'badge-ghost', 1: 'badge-info', 2: 'badge-success',
        3: 'badge-error', 4: 'badge-ghost', 5: 'badge-warning', 6: 'badge-warning',
    };

    function connect() {
        socket = new WebSocket(wsUrl);

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
        // Parent run update → status bar badge
        if (event.run_id === runId) {
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
        var el = document.getElementById('workflow-name');
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

    connect();
})();
