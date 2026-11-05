/**
 * PowerShell Terminal - xterm.js client over a WebSocket.
 * Reconnects automatically, including when another Claude session takes over.
 */

(async function() {
    // Wait for Terminal and FitAddon to load
    while (typeof Terminal === 'undefined' || typeof FitAddon === 'undefined') {
        await new Promise(r => setTimeout(r, 100));
    }
    
    // ========== TERMINAL INITIALIZATION ==========
    
    const term = new Terminal({
        cursorBlink: true, 
        fontSize: 14,
        fontFamily: 'Consolas, "Courier New", monospace',
        theme: { 
            background: '#1e1e1e', 
            foreground: '#cccccc', 
            cursor: '#00ff00' 
        },
        scrollback: 10000,
        convertEol: false
    });
    
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById('terminal'));
    
    await new Promise(r => setTimeout(r, 100));
    fitAddon.fit();

    // Banner is emitted server-side by the session init script (rendered through
    // ConPTY so cursor rows stay in sync). No client-side banner here.
    
    // ========== WEBSOCKET CONNECTION ==========
    //
    // Several Claude sessions can share this port: the latest one to use the
    // terminal owns it. When ownership moves, the old owner sends
    // 'session_handover' and stops; this tab then reconnects to the new owner,
    // which clears the screen and replays its own shell. When the Claude session
    // ends, the server sends 'server_shutdown' and the tab closes itself; if the
    // server just disappears (killed), the tab retries for DISCONNECT_WINDOW_MS
    // and then closes. Browsers may refuse to close a tab (e.g. one you navigated
    // in); the tab then says so instead.

    const DISCONNECT_WINDOW_MS = 5000;    // server gone: retry this long, then close
    const HANDOVER_WINDOW_MS = 20000;     // take-over: new owner normally answers in ~2 s
    const RECONNECT_DELAY_MS = 500;
    const CONNECT_TIMEOUT_MS = 1000;      // abandon an attempt Windows is slow to refuse
    const HANDOVER_PAUSE_MS = 1500;   // keep the take-over notice readable

    let ws = null;
    let intentionalClose = false;
    let pauseUntil = 0;            // no reconnect attempt before this time
    let reconnectDeadline = 0;     // 0 = not currently reconnecting
    let reconnectTimer = null;
    let everConnected = false;

    function connectWebSocket() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const sock = new WebSocket(`${protocol}//${window.location.host}/ws/terminal`);
        ws = sock;
        // Windows takes ~2 s per address (~4 s for 'localhost') to refuse a
        // connection when nothing is listening yet -- e.g. during a take-over,
        // just before the new owner binds the port. A local server answers in
        // milliseconds, so an attempt still pending after 1 s is abandoned and
        // the reconnect loop tries again right away.
        setTimeout(() => {
            if (sock === ws && sock.readyState === WebSocket.CONNECTING) {
                ws = null;
                try { sock.close(); } catch (e) { /* ignore */ }
                if (!intentionalClose) startReconnect(false, DISCONNECT_WINDOW_MS);
            }
        }, CONNECT_TIMEOUT_MS);

        sock.onopen = () => {
            if (sock !== ws) return;
            console.log('WebSocket connected');
            if (everConnected) {
                // Reconnected (possibly to a different session): start clean.
                // The server replays the current screen right after this.
                term.reset();
            }
            everConnected = true;
            reconnectDeadline = 0;
            // The initial fitAddon.fit() fired before the socket was open, so its
            // resize was dropped and ConPTY is still at its spawn size. Re-send the
            // current size now so ConPTY matches xterm (prevents line-wrap drift).
            try {
                sock.send(JSON.stringify({
                    type: 'terminal_resize',
                    cols: term.cols,
                    rows: term.rows
                }));
            } catch (e) { console.warn('initial resize send failed:', e); }
            sendVisibility();
        };

        sock.onmessage = (event) => {
            if (sock !== ws) return;
            try {
                const message = JSON.parse(event.data);

                if (message.type === 'terminal_output') {
                    term.write(message.data);
                }
                else if (message.type === 'session_handover') {
                    term.writeln('\r\n\x1b[33m-- Terminal taken over by another Claude session, reconnecting... --\x1b[0m');
                    pauseUntil = Date.now() + HANDOVER_PAUSE_MS;
                    startReconnect(false, HANDOVER_WINDOW_MS);
                }
                else if (message.type === 'session_superseded') {
                    // A fresh tab was opened on purpose; step aside.
                    closeTab('This terminal was reopened in a new tab.');
                }
                else if (message.type === 'server_shutdown') {
                    closeTab('Claude session ended.');
                }
                else if (message.type === 'notice') {
                    // Shown in the header, not the terminal: writing lines the
                    // shell doesn't know about would leave no prompt and could
                    // throw off later redraws.
                    flashHeader(message.text || '');
                }
                else if (message.type === 'owner') {
                    // Which Claude session owns the terminal now.
                    const label = message.label || '';
                    document.title = label ? `PowerShell Terminal — ${label}` : 'PowerShell Terminal';
                    const el = document.getElementById('pt-owner');
                    if (el) {
                        el.dataset.label = label;
                        if (!flashTimer) el.textContent = label;
                        el.title = message.pid ? `MCP server process ${message.pid}` : '';
                    }
                }
                else if (message.type === 'connection') {
                    console.log('Connection status:', message.status);
                }
            } catch (e) {
                console.error('Error processing WebSocket message:', e);
            }
        };

        sock.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        sock.onclose = () => {
            if (sock !== ws) return;
            console.log('WebSocket closed');
            if (intentionalClose) {
                console.log('Intentional close, ignoring.');
                return;
            }
            startReconnect(true, DISCONNECT_WINDOW_MS);
        };
    }

    function sendVisibility() {
        // Tell the server whether this tab is on screen. open_terminal reuses
        // only a visible tab; a hidden one (background tab, minimized window,
        // a browser pane nobody looks at) would otherwise hide the terminal.
        if (ws && ws.readyState === WebSocket.OPEN) {
            try {
                ws.send(JSON.stringify({ type: 'visibility', visible: !document.hidden }));
            } catch (e) { /* ignore */ }
        }
    }

    let flashTimer = null;
    function flashHeader(text) {
        // Briefly replace the owner label with a highlighted notice.
        const el = document.getElementById('pt-owner');
        if (!el) return;
        if (!flashTimer) el.dataset.label = el.textContent;
        el.textContent = text;
        el.classList.add('flash');
        clearTimeout(flashTimer);
        flashTimer = setTimeout(() => {
            el.classList.remove('flash');
            el.textContent = el.dataset.label || '';
            flashTimer = null;
        }, 3000);
    }

    function closeTab(reason) {
        // Stop for good, then try to close the tab. Browsers allow this for a
        // tab with a single history entry (how the terminal tab is opened);
        // otherwise leave one clear line instead.
        intentionalClose = true;
        reconnectDeadline = 0;
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        try { if (ws) ws.close(); } catch (e) { /* already closed */ }
        window.close();
        setTimeout(() => {
            term.writeln('\r\n\x1b[33m-- ' + reason + ' You can close this tab. --\x1b[0m');
        }, 300);
    }

    function startReconnect(announce, windowMs) {
        if (reconnectDeadline === 0) {
            reconnectDeadline = Date.now() + windowMs;
            if (announce) {
                term.writeln('\r\n\x1b[33m-- Disconnected, reconnecting... --\x1b[0m');
            }
        }
        scheduleReconnect();
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        const delay = Math.max(RECONNECT_DELAY_MS, pauseUntil - Date.now());
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (ws && ws.readyState === WebSocket.OPEN) return;
            if (reconnectDeadline && Date.now() > reconnectDeadline) {
                closeTab('Claude session ended.');
                return;
            }
            connectWebSocket();
            scheduleReconnect();   // keep trying until onopen or the window ends
        }, delay);
    }

    // Close the WebSocket when the page unloads
    window.addEventListener('beforeunload', () => {
        intentionalClose = true;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.close();
        }
    });

    // Reconnect when the tab becomes visible with a dead connection (covers a
    // tab that gave up while hidden).
    document.addEventListener('visibilitychange', () => {
        sendVisibility();
        if (!document.hidden && !intentionalClose && ws && ws.readyState !== WebSocket.OPEN) {
            console.log('Tab became visible with dead connection - forcing reconnect');
            reconnectDeadline = 0;
            startReconnect(false, DISCONNECT_WINDOW_MS);
            if (Date.now() >= pauseUntil) connectWebSocket();
        }
    });

    // Initial connection
    connectWebSocket();
    
    // ========== USER INPUT HANDLING ==========
    
    term.onData(data => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'terminal_input',
                data: data
            }));
        }
    });
    
    // ========== TERMINAL RESIZE ==========
    
    term.onResize(({cols, rows}) => {
        console.log('Terminal resized to:', cols, 'x', rows);
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'terminal_resize',
                cols: cols,
                rows: rows
            }));
        }
    });
    
    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            fitAddon.fit();
        }, 100);
    });
    
    // ========== COPY/PASTE FUNCTIONALITY ==========
    
    const terminalElement = document.getElementById('terminal');
    let contextMenu = null;
    
    function copySelection() {
        const selection = term.getSelection();
        if (selection) {
            navigator.clipboard.writeText(selection)
                .then(() => console.log('Copied to clipboard'))
                .catch(err => console.warn('Copy failed:', err));
        }
    }
    
    function pasteFromClipboard() {
        navigator.clipboard.readText()
            .then(text => {
                if (text) {
                    term.paste(text);
                }
            })
            .catch(err => console.warn('Paste failed:', err));
    }
    
    terminalElement.addEventListener('paste', (e) => {
        e.preventDefault();
        const text = e.clipboardData?.getData('text');
        if (text) {
            term.paste(text);
        }
    });
    
    term.attachCustomKeyEventHandler((event) => {
        const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
        const modKey = isMac ? event.metaKey : event.ctrlKey;
        
        if (event.type !== 'keydown') return true;
        
        if (modKey && event.key.toLowerCase() === 'c') {
            const hasSelection = term.hasSelection();
            
            if (event.shiftKey || hasSelection) {
                copySelection();
                return false;
            } else if (!hasSelection) {
                return true;
            }
        }
        
        if (modKey && event.shiftKey && event.key.toLowerCase() === 'v') {
            pasteFromClipboard();
            return false;
        }
        
        return true;
    });
    
    // ========== CONTEXT MENU ==========
    
    function removeContextMenu() {
        if (contextMenu) {
            contextMenu.remove();
            contextMenu = null;
        }
    }
    
    function showContextMenu(x, y) {
        removeContextMenu();
        
        const hasSelection = term.hasSelection();
        
        contextMenu = document.createElement('div');
        contextMenu.className = 'context-menu';
        contextMenu.style.left = x + 'px';
        contextMenu.style.top = y + 'px';
        
        const copyItem = document.createElement('div');
        copyItem.className = 'context-menu-item' + (hasSelection ? '' : ' disabled');
        copyItem.textContent = 'Copy';
        if (hasSelection) {
            copyItem.onclick = () => {
                copySelection();
                removeContextMenu();
            };
        }
        contextMenu.appendChild(copyItem);
        
        const pasteItem = document.createElement('div');
        pasteItem.className = 'context-menu-item';
        pasteItem.textContent = 'Paste';
        pasteItem.onclick = () => {
            pasteFromClipboard();
            removeContextMenu();
        };
        contextMenu.appendChild(pasteItem);
        
        const separator = document.createElement('div');
        separator.className = 'context-menu-separator';
        contextMenu.appendChild(separator);
        
        const selectAllItem = document.createElement('div');
        selectAllItem.className = 'context-menu-item';
        selectAllItem.textContent = 'Select All';
        selectAllItem.onclick = () => {
            term.selectAll();
            removeContextMenu();
        };
        contextMenu.appendChild(selectAllItem);
        
        const clearItem = document.createElement('div');
        clearItem.className = 'context-menu-item';
        clearItem.textContent = 'Clear Terminal';
        clearItem.onclick = () => {
            term.clear();
            removeContextMenu();
        };
        contextMenu.appendChild(clearItem);
        
        document.body.appendChild(contextMenu);
        
        const rect = contextMenu.getBoundingClientRect();
        if (rect.right > window.innerWidth) {
            contextMenu.style.left = (x - rect.width) + 'px';
        }
        if (rect.bottom > window.innerHeight) {
            contextMenu.style.top = (y - rect.height) + 'px';
        }
    }
    
    terminalElement.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        showContextMenu(e.pageX, e.pageY);
    });
    
    document.addEventListener('click', removeContextMenu);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            removeContextMenu();
        }
    });
    
    term.focus();
    
})();
