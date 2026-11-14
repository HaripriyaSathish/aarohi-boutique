(() => {
    const byId = id => document.getElementById('aarohi-ai-' + id);
    const launch = byId('launch');
    if (!launch) return;

    const panel = byId('panel');
    const form = byId('form');
    const input = byId('input');
    const messages = byId('messages');
    const status = byId('status');
    const send = byId('send');
    const mic = byId('mic');
    const language = byId('language');
    let recognition;

    function open() {
        panel.hidden = false;
        launch.setAttribute('aria-expanded', 'true');
        input.focus();
    }

    function close() {
        panel.hidden = true;
        launch.setAttribute('aria-expanded', 'false');
        launch.focus();
    }

    function append(element) {
        messages.append(element);
        messages.scrollTop = messages.scrollHeight;
    }

    // Scroll so the given element sits at the top of the chat log — used to
    // show the start of a reply instead of jumping past it to the bottom.
    function revealFrom(element) {
        if (!element || !element.isConnected) return;
        const offset = element.getBoundingClientRect().top - messages.getBoundingClientRect().top;
        messages.scrollTo({ top: messages.scrollTop + offset - 8, behavior: 'smooth' });
    }

    // Message box starts one line tall and grows with the text (up to the CSS max-height).
    function fitInput() {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight + 2, 132) + 'px';
    }
    input.addEventListener('input', fitInput);
    // Collapsible "Choose an assistant" picker
    const agents = panel.querySelector('.aarohi-ai-agents');
    const agentsToggle = byId('agents-toggle');
    const agentsCurrent = byId('agents-current');

    function setAgentsOpen(isOpen) {
        if (!agents || !agentsToggle) return;
        agents.classList.toggle('is-collapsed', !isOpen);
        agentsToggle.setAttribute('aria-expanded', String(isOpen));
    }

    function syncAgentLabel() {
        const checked = panel.querySelector('input[name=aarohi-agent]:checked');
        if (agentsCurrent && checked) agentsCurrent.textContent = checked.nextElementSibling.textContent.trim();
    }

    if (agentsToggle) {
        agentsToggle.addEventListener('click', () => setAgentsOpen(agents.classList.contains('is-collapsed')));
        panel.querySelectorAll('input[name=aarohi-agent]').forEach(radio => radio.addEventListener('change', () => {
            syncAgentLabel();
            setAgentsOpen(false);
            input.focus();
        }));
        syncAgentLabel();
        // Start folded so the conversation gets the space; tap the bar to change assistant.
        setAgentsOpen(false);
    }

    // Minimal, safe markdown: text is HTML-escaped first, then **bold** and
    // "- " / "* " bullet lines are turned into markup.
    function renderMarkdown(text) {
        const escape = value => value.replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
        const inline = value => escape(value)
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            // only same-site relative links like [Review](/assistant/checkout/...)
            .replace(/\[([^\]]+)\]\((\/[A-Za-z0-9\/_\-?=&;.%]*)\)/g,
                '<a class="aarohi-ai-inline-link" href="$2">$1</a>');
        let html = '', inList = false;
        for (const raw of String(text).split('\n')) {
            const line = raw.trim();
            const item = line.match(/^[-*•]\s+(.*)$/);
            if (item) {
                if (!inList) { html += '<ul>'; inList = true; }
                html += '<li>' + inline(item[1]) + '</li>';
                continue;
            }
            if (inList) { html += '</ul>'; inList = false; }
            if (line) html += '<p>' + inline(line.replace(/^#+\s*/, '')) + '</p>';
        }
        if (inList) html += '</ul>';
        return html;
    }

    // Fabric colour names -> swatch colours (unknown names fall back to CSS, then grey)
    const SWATCHES = {
        lavender: '#b9a2db', lilac: '#c8a2c8', purple: '#7b4fa3', violet: '#7f52a8', mauve: '#b784a7',
        plum: '#6b3a63', wine: '#722f37', ivory: '#fffff0', cream: '#f8efd8', white: '#ffffff',
        rose: '#e8a0b4', pink: '#f4a7c0', peach: '#ffcba4', blush: '#f2c4c4', coral: '#f08070',
        red: '#c0392b', maroon: '#7b1e2b', burgundy: '#800020', sage: '#9cae8f', green: '#4f8a5b',
        olive: '#808044', mint: '#a8dcc0', blue: '#3b6ea8', navy: '#1f2e5a', indigo: '#3f4c9a',
        teal: '#2a8c8c', yellow: '#f2c94c', mustard: '#d4a12a', gold: '#c8a165', black: '#222222',
        grey: '#9a9a9a', gray: '#9a9a9a', beige: '#e3d3b6', sand: '#d8c3a0', brown: '#8b5e3c',
        'as pictured': 'linear-gradient(135deg,#e8a0b4,#c8a165)'
    };
    function swatchColour(name) {
        const key = name.toLowerCase();
        if (SWATCHES[key]) return SWATCHES[key];
        const word = Object.keys(SWATCHES).find(k => key.includes(k));
        if (word) return SWATCHES[word];
        return CSS.supports('color', key) ? key : '#cfc6d8';
    }

    function bubble(text, user = false) {
        const element = document.createElement('div');
        element.className = user ? 'aarohi-ai-bubble user' : 'aarohi-ai-bubble';
        if (user) element.textContent = text;
        else element.innerHTML = renderMarkdown(text);
        append(element);
    }

    function link(url, prefix, label, parent = messages) {
        if (typeof url !== 'string' || !url.startsWith(prefix)) return;
        const target = new URL(url, location.origin);
        if (target.origin !== location.origin || !target.pathname.startsWith(prefix)) return;
        const element = document.createElement('a');
        element.href = target.href;
        element.className = 'aarohi-ai-product';
        element.textContent = label;
        parent.append(element);
        messages.scrollTop = messages.scrollHeight;
    }

    launch.addEventListener('click', () => panel.hidden ? open() : close());
    byId('close').addEventListener('click', close);
    document.addEventListener('click', event => {
        if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey
            || event.shiftKey || event.altKey) return;
        const anchor = event.target.closest('a[href]');
        if (anchor?.origin === location.origin && anchor.pathname === launch.dataset.assistantUrl
            && !anchor.search && !anchor.target) {
            event.preventDefault();
            open();
        }
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !panel.hidden) close();
    });
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        const message = input.value.trim();
        if (!message || send.disabled) return;
        input.value = '';
        fitInput();
        send.disabled = true;
        setAgentsOpen(false);
        bubble(message, true);
        const question = messages.lastElementChild;
        status.textContent = 'Aarohi AI is working…';
        const typing = document.createElement('div');
        typing.className = 'aarohi-ai-bubble aarohi-ai-typing';
        typing.setAttribute('aria-label', 'Aarohi AI is typing');
        typing.innerHTML = '<i></i><i></i><i></i>';
        append(typing);
        try {
            const headers = {
                'Content-Type': 'application/json',
                'X-CSRFToken': form.elements.csrfmiddlewaretoken.value
            };
            if (window.crypto?.randomUUID) {
                headers['Idempotency-Key'] = window.crypto.randomUUID();
            }
            const response = await fetch(form.action, {
                method: 'POST',
                credentials: 'same-origin',
                headers,
                body: JSON.stringify({
                    message,
                    agent: panel.querySelector('input[name=aarohi-agent]:checked').value,
                    language: language.value,
                    authorize_routine_updates: true
                })
            });
            if (response.redirected && new URL(response.url).pathname === form.dataset.loginUrl) {
                bubble('Sign in to shop with Aarohi AI and access your orders.');
                link(form.dataset.loginUrl + '?next=' + encodeURIComponent(location.pathname + location.search),
                    form.dataset.loginUrl, 'Sign in');
                input.value = message; fitInput();
                return;
            }
            if (!response.headers.get('Content-Type')?.includes('application/json')) {
                throw new Error('The store returned an unexpected response (' + response.status + '). Refresh the page and try again.');
            }
            const data = await response.json();
            typing.remove();
            if (!response.ok) throw new Error(data.error || 'The assistant could not complete your request.');

            bubble(data.answer || 'Your request was received.');
            const products = (data.products || []).filter(p => typeof p.url === 'string' && p.url.startsWith('/product/'));
            if (products.length) {
                const rail = document.createElement('div');
                rail.className = 'aarohi-ai-cards';
                for (const product of products) {
                    const card = document.createElement('article');
                    card.className = 'aarohi-ai-card';
                    const photo = document.createElement('a');
                    photo.href = product.url;
                    photo.className = 'aarohi-ai-card-photo';
                    if (typeof product.image === 'string' && product.image.startsWith('/media/')) {
                        const img = document.createElement('img');
                        img.src = product.image;
                        img.alt = product.name;
                        img.loading = 'lazy';
                        photo.append(img);
                    }
                    const name = document.createElement('a');
                    name.href = product.url;
                    name.className = 'aarohi-ai-card-name';
                    name.textContent = product.name;
                    const price = document.createElement('strong');
                    price.textContent = '₹' + product.price_inr;
                    // Colour swatches + sizes from in-stock variants
                    const inStock = (product.variants || []).filter(v => v.stock > 0);
                    const colours = [...new Set(inStock.map(v => String(v.colour).trim()).filter(Boolean)
                        .map(c => c.charAt(0).toUpperCase() + c.slice(1).toLowerCase()))];
                    const ORDER = ['XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL', '3XL', '4XL'];
                    const rank = s => { const i = ORDER.indexOf(s.toUpperCase()); return i < 0 ? 99 : i; };
                    const sizes = [...new Set(inStock.map(v => String(v.size).trim()).filter(Boolean))]
                        .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b, undefined, { numeric: true }));
                    const meta = document.createElement('div');
                    meta.className = 'aarohi-ai-card-meta';
                    if (colours.length) {
                        const row = document.createElement('div');
                        row.className = 'aarohi-ai-swatches';
                        for (const colour of colours.slice(0, 5)) {
                            const dot = document.createElement('span');
                            dot.className = 'aarohi-ai-swatch';
                            dot.title = colour;
                            dot.style.background = swatchColour(colour);
                            row.append(dot);
                        }
                        const label = document.createElement('small');
                        label.textContent = colours.slice(0, 3).join(' · ') + (colours.length > 3 ? ' +' + (colours.length - 3) : '');
                        row.append(label);
                        meta.append(row);
                    }
                    if (sizes.length) {
                        const sizeLine = document.createElement('small');
                        sizeLine.className = 'aarohi-ai-sizes';
                        sizeLine.textContent = 'Sizes: ' + (sizes.length > 4 ? sizes[0] + '–' + sizes[sizes.length - 1] : sizes.join(', '));
                        meta.append(sizeLine);
                    }
                    const add = document.createElement('button');
                    add.type = 'button';
                    add.className = 'aarohi-ai-card-add';
                    add.textContent = 'Add to bag';
                    add.addEventListener('click', () => {
                        input.value = 'Add the ' + product.name + ' to my bag';
                        const cart = panel.querySelector('input[name=aarohi-agent][value=cart]');
                        if (cart) cart.checked = true;
                        form.requestSubmit();
                    });
                    card.append(photo, name, price, meta, add);
                    rail.append(card);
                }
                append(rail);
            }
            for (const action of data.actions || []) {
                link(action.confirm_url, '/assistant/actions/', 'Review: ' + action.summary);
            }
            for (const journey of data.journeys || []) {
                const card = document.createElement('article');
                card.className = 'aarohi-ai-checkout';
                for (const item of journey.items || []) {
                    const row = document.createElement('div');
                    row.className = 'aarohi-ai-checkout-item';
                    if (typeof item.image === 'string' && item.image.startsWith('/media/')) {
                        const photo = document.createElement('img');
                        photo.src = item.image;
                        photo.alt = '';
                        row.append(photo);
                    }
                    const title = document.createElement('strong');
                    title.textContent = item.name + ' × ' + item.quantity;
                    row.append(title);
                    card.append(row);
                }
                const total = document.createElement('strong');
                total.textContent = '₹' + journey.total_inr;
                card.append(total);
                append(card);
                link(journey.review_url, '/assistant/checkout/', 'Review checkout', card);
            }
            for (const order of data.orders || []) {
                link(order.url, '/orders/', order.reference + ' · ' + order.status);
            }
            if (window.speechSynthesis && data.answer) {
                const listen = document.createElement('button');
                listen.type = 'button';
                listen.className = 'aarohi-ai-product';
                listen.textContent = 'Listen';
                listen.addEventListener('click', () => {
                    window.speechSynthesis.cancel();
                    const speech = new SpeechSynthesisUtterance(data.answer);
                    speech.lang = /[\u0B80-\u0BFF]/.test(data.answer) ? 'ta-IN' : 'en-IN';
                    window.speechSynthesis.speak(speech);
                });
                append(listen);
            }
        } catch (error) {
            bubble(error.message);
            if (!input.value) input.value = message;
            fitInput();
        } finally {
            typing.remove();
            send.disabled = false;
            status.textContent = '';
            // Show the user's question with the beginning of the reply right under it.
            requestAnimationFrame(() => revealFrom(question));
        }
    });

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    mic.disabled = !SpeechRecognition;
    if (!SpeechRecognition) mic.title = 'Voice recognition is unavailable in this browser';
    mic.addEventListener('click', () => {
        if (recognition) {
            recognition.stop();
            return;
        }
        recognition = new SpeechRecognition();
        recognition.lang = language.value === 'ta-IN' ? 'ta-IN' : 'en-IN';
        recognition.onresult = event => {
            input.value = event.results[0][0].transcript;
            fitInput();
            input.focus();
        };
        recognition.onerror = () => {
            status.textContent = 'Voice recognition failed. You can type your message.';
        };
        recognition.onend = () => {
            recognition = null;
            mic.textContent = '🎤';
            mic.classList.remove('listening');
        };
        try {
            recognition.start();
            mic.textContent = '■';
            mic.classList.add('listening');
            status.textContent = '';
        } catch {
            recognition = null;
            status.textContent = 'Voice recognition could not start.';
        }
    });
})();
