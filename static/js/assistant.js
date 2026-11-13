'use strict';

const form = document.querySelector('#assistant-form');
const input = document.querySelector('#assistant-input');
const messages = document.querySelector('#chat-messages');
const statusLine = document.querySelector('#chat-status');
const sendButton = document.querySelector('#assistant-send');
const agentSelect = document.querySelector('#agent-select');

const csrf = form.elements.csrfmiddlewaretoken.value;

const agentCards = [...document.querySelectorAll('.agent-choice')];
const activeAgentName = document.querySelector('#active-agent-name');
function syncAgentCards() {
    agentCards.forEach(card => {
        const selected = card.dataset.agent === agentSelect.value;
        card.classList.toggle('is-active', selected);
        card.setAttribute('aria-pressed', String(selected));
    });
    const selectedCard = agentCards.find(card => card.dataset.agent === agentSelect.value);
    if (activeAgentName && selectedCard) {
        activeAgentName.textContent = selectedCard.querySelector('strong').textContent.trim();
    }
}
agentCards.forEach(card => card.addEventListener('click', () => {
    if (busy) return;
    agentSelect.value = card.dataset.agent;
    agentSelect.dispatchEvent(new Event('change', {bubbles:true}));
    syncAgentCards();
    input.focus();
}));
agentSelect.addEventListener('change', syncAgentCards);


let busy = false;
let stopVoiceInput = () => {};

statusLine.setAttribute('role', 'status');
statusLine.setAttribute('aria-live', 'polite');

const requested = new URLSearchParams(
    location.search
).get('agent');

if (
    [...agentSelect.options].some(
        option => option.value === requested
    )
) {
    agentSelect.value = requested;
}

syncAgentCards();

function localPath(path, prefix) {
    return (
        typeof path === 'string' &&
        path.startsWith(prefix) &&
        !path.startsWith('//') &&
        !path.includes('\\')
    );
}

function element(tag, className = '', text = '') {
    const node = document.createElement(tag);

    node.className = className;
    node.textContent = text;

    return node;
}

function readReply(text, button) {
    if (!('speechSynthesis' in window)) {
        return;
    }

    const speech = window.speechSynthesis;

    if (button.dataset.reading === 'yes') {
        speech.cancel();

        button.dataset.reading = 'no';
        button.textContent = 'Listen 🔊';

        return;
    }

    stopVoiceInput();

    const tamil = /[\u0B80-\u0BFF]/.test(text);

    const voice = speech.getVoices().find(item => {
        return item.lang
            .toLowerCase()
            .startsWith(tamil ? 'ta' : 'en');
    });

    if (tamil && !voice) {
        statusLine.textContent =
            'No Tamil reading voice is available in this browser. Text replies still work.';

        return;
    }

    speech.cancel();

    const utterance = new SpeechSynthesisUtterance(text);

    utterance.lang = tamil ? 'ta-IN' : 'en-IN';
    utterance.rate = 0.95;

    if (voice) {
        utterance.voice = voice;
    }

    utterance.onstart = () => {
        button.dataset.reading = 'yes';
        button.textContent = 'Stop reading ■';
    };

    const reset = () => {
        button.dataset.reading = 'no';
        button.textContent = 'Listen 🔊';
    };

    utterance.onend = reset;
    utterance.onerror = reset;

    speech.speak(utterance);
}

function bubble(text, user = false) {
    const node = element(
        'div',
        'chat-bubble ' +
            (user ? 'user-bubble' : 'assistant-bubble'),
        text || ''
    );

    if (
        !user &&
        text &&
        'speechSynthesis' in window
    ) {
        const controls = element('div');

        const listen = element(
            'button',
            'clear-link',
            'Listen 🔊'
        );

        listen.type = 'button';

        listen.addEventListener('click', () => {
            readReply(text, listen);
        });

        controls.append(listen);
        node.append(controls);
    }

    messages.append(node);

    messages.scrollTop = messages.scrollHeight;
}

async function jsonResponse(response) {
    if (response.redirected) {
        throw new Error(
            'Session expired. Please sign in again.'
        );
    }

    const contentType =
        response.headers.get('content-type') || '';

    if (!contentType.includes('application/json')) {
        throw new Error(
            'The server returned an unexpected response. Check the Django terminal.'
        );
    }

    const data = await response.json();

    if (!response.ok) {
        throw new Error(
            data.error || 'Request failed.'
        );
    }

    return data;
}

function showProducts(products = []) {
    if (!products.length) {
        return;
    }

    const grid = element('div', 'agent-products');

    products.forEach(product => {
        if (!localPath(product.url, '/product/')) {
            return;
        }

        const card = element(
            'article',
            'agent-product'
        );

        const link = element('a');

        link.href = product.url;

        if (localPath(product.image, '/media/')) {
            const image = element('img');

            image.src = product.image;
            image.alt = product.name;
            image.loading = 'lazy';

            link.append(image);
        }

        link.append(
            element('span', '', product.name)
        );

        link.append(
            element(
                'strong',
                '',
                '₹' + product.price_inr
            )
        );

        card.append(link);

        const variants = (
            product.variants || []
        ).filter(variant => {
            return variant.stock > 0 && variant.id;
        });

        if (variants.length) {
            const select = element('select');

            select.setAttribute(
                'aria-label',
                'Choose size and colour for ' +
                    product.name
            );

            variants.forEach(variant => {
                const option = element(
                    'option',
                    '',
                    variant.size +
                        ' · ' +
                        variant.colour
                );

                option.value = variant.id;

                select.append(option);
            });

            const add = element(
                'button',
                'secondary',
                'Prepare add to bag'
            );

            add.type = 'button';

            add.addEventListener('click', () => {
                if (busy) {
                    return;
                }

                stopVoiceInput();

                agentSelect.value = 'cart';

                input.value =
                    'add ' + select.value + ' 1';

                form.requestSubmit();
            });

            card.append(select, add);
        }

        grid.append(card);
    });

    messages.append(grid);
}

function showActions(actions = []) {
    actions.forEach(action => {
        if (
            !localPath(
                action.confirm_url,
                '/assistant/actions/'
            )
        ) {
            return;
        }

        const card = element(
            'article',
            'agent-action-card'
        );

        const title = element(
            'strong',
            '',
            'Review proposed action'
        );

        const details = element(
            'p',
            '',
            action.summary
        );

        const confirm = element(
            'button',
            '',
            'Confirm this exact action'
        );

        confirm.type = 'button';

        const review = element(
            'a',
            'clear-link',
            'Open action details'
        );

        review.href =
            '/assistant/actions/' +
            encodeURIComponent(action.id) +
            '/';

        confirm.addEventListener('click', async () => {
            confirm.disabled = true;

            try {
                const response = await fetch(
                    action.confirm_url,
                    {
                        method: 'POST',

                        headers: {
                            'X-CSRFToken': csrf,
                            'Accept': 'application/json'
                        }
                    }
                );

                const data = await jsonResponse(
                    response
                );

                bubble(data.message);

                confirm.textContent = 'Completed';

                if (localPath(data.url, '/')) {
                    const link = element(
                        'a',
                        'button secondary',
                        'View result →'
                    );

                    link.href = data.url;

                    card.append(link);
                }
            } catch (error) {
                bubble(error.message);

                confirm.disabled = false;
            }
        });

        card.append(
            title,
            details,
            confirm,
            review
        );

        messages.append(card);
    });
}

form.addEventListener('submit', async event => {
    event.preventDefault();

    const message = input.value.trim();

    if (busy || !message) {
        return;
    }

    const selectedAgent = agentSelect.value;

    stopVoiceInput();

    busy = true;

    sendButton.disabled = true;
    agentSelect.disabled = true;

    input.value = '';

    bubble(message, true);

    statusLine.textContent =
        'Checking store information…';

    const controller = new AbortController();

    const deadline = setTimeout(() => {
        controller.abort();
    }, 270000);

    try {
        const response = await fetch(form.action, {
            method: 'POST',

            signal: controller.signal,

            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf,
                'Idempotency-Key': crypto.randomUUID()
            },

            body: JSON.stringify({
                message: message,
                agent: selectedAgent,
                language: document.querySelector("#voice-language")?.value || "auto",
                authorize_routine_updates: document.querySelector("#authorize-routine")?.checked === true
            })
        });

        const data = await jsonResponse(response);

        bubble(data.answer);

        showProducts(data.products || []);
        showActions(data.actions || []);
        (data.journeys || []).forEach(journey => {
            if (!localPath(journey.review_url, '/assistant/checkout/')) return;
            const card = element('article', 'agent-action-card');
            card.append(element('strong', '', 'Your order is ready to review · ₹' + journey.total_inr));
            const link = element('a', 'button', 'Review items & authorize checkout ↗');
            link.href = journey.review_url;
            card.append(link);
            messages.append(card);
        });
        if (document.querySelector('#auto-speak')?.checked && data.answer) {
            readReply(data.answer, document.createElement('button'));
        }

        (data.orders || []).forEach(order => {
            if (
                !localPath(order.url, '/orders/')
            ) {
                return;
            }

            const link = element(
                'a',
                'agent-order-link',
                order.reference +
                    ' — ' +
                    order.status +
                    ' →'
            );

            link.href = order.url;

            messages.append(link);
        });

        const label =
            data.engine === 'Verified order workflow'
                ? data.engine
                : (
                    data.mode === 'local'
                        ? 'Rules-based demo'
                        : 'AI'
                );

        messages.append(
            element(
                'small',
                'agent-caption',
                data.agent +
                    ' · ' +
                    label +
                    ' · Store tools: ' +
                    (data.tools || []).join(', ')
            )
        );

        statusLine.textContent = '';

        messages.scrollTop =
            messages.scrollHeight;
    } catch (error) {
        statusLine.textContent =
            error.name === 'AbortError'
                ? 'Request timed out. Check provider availability before retrying.'
                : error.message;
    } finally {
        clearTimeout(deadline);

        busy = false;

        sendButton.disabled = false;
        agentSelect.disabled = false;

        input.focus();
    }
});

document.querySelectorAll(
    '[data-prompt]'
).forEach(button => {
    button.addEventListener('click', () => {
        if (busy) {
            return;
        }

        stopVoiceInput();

        input.value = button.dataset.prompt;

        form.requestSubmit();
    });
});

document.querySelector(
    '#clear-chat'
)?.addEventListener('click', async () => {
    if (busy) {
        return;
    }

    stopVoiceInput();

    if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
    }

    try {
        const response = await fetch(
            '/assistant/clear/',
            {
                method: 'POST',

                headers: {
                    'X-CSRFToken': csrf
                }
            }
        );

        await jsonResponse(response);

        messages.replaceChildren();

        bubble('Chat cleared. How can I help you?');

        statusLine.textContent = '';
    } catch (error) {
        statusLine.textContent = error.message;
    }
});

// Voice is an input method.
// It must not replace the selected specialist agent.

const voiceButton = document.querySelector(
    '#voice-input'
);

const voiceLanguage = document.querySelector(
    '#voice-language'
);

const SpeechRecognition =
    window.SpeechRecognition ||
    window.webkitSpeechRecognition;

if (voiceButton) {
    if (
        !SpeechRecognition ||
        window.isSecureContext === false
    ) {
        voiceButton.disabled = true;

        voiceButton.textContent =
            'Voice unavailable. Use Chrome on localhost or HTTPS.';
    } else {
        const recognition = new SpeechRecognition();

        recognition.interimResults = true;
        recognition.continuous = false;
        recognition.maxAlternatives = 1;

        let active = false;
        let ending = false;
        let acceptResults = false;
        let received = false;
        let failed = false;
        let original = '';

        function resetVoice() {
            active = false;
            ending = false;

            voiceButton.disabled = false;
            voiceButton.textContent = 'Use voice';

            voiceButton.setAttribute(
                'aria-pressed',
                'false'
            );

            if (voiceLanguage) {
                voiceLanguage.disabled = false;
            }

            agentSelect.disabled = busy;
        }

        stopVoiceInput = () => {
            acceptResults = false;

            if (!active) {
                return;
            }

            ending = true;

            try {
                recognition.abort();
            } catch (error) {
                resetVoice();
            }
        };

        voiceButton.addEventListener('click', () => {
            if (busy || ending) {
                return;
            }

            if (active) {
                ending = true;

                voiceButton.textContent =
                    'Stopping…';

                try {
                    recognition.stop();
                } catch (error) {
                    resetVoice();
                }

                return;
            }

            if ('speechSynthesis' in window) {
                window.speechSynthesis.cancel();
            }

            original = input.value.trim();

            received = false;
            failed = false;
            acceptResults = true;
            active = true;

            agentSelect.disabled = true;

            if (voiceLanguage) {
                voiceLanguage.disabled = true;
            }

            recognition.lang =
                voiceLanguage?.value === 'tanglish' ? 'ta-IN' : (voiceLanguage?.value || 'en-IN');

            voiceButton.textContent =
                'Starting microphone…';

            statusLine.textContent =
                'Allow microphone access, then speak.';

            try {
                recognition.start();
            } catch (error) {
                failed = true;
                acceptResults = false;

                resetVoice();

                statusLine.textContent =
                    'Could not start microphone: ' +
                    error.name;
            }
        });

        recognition.onstart = () => {
            if (ending) {
                return;
            }

            voiceButton.textContent =
                'Listening… tap to stop';

            voiceButton.setAttribute(
                'aria-pressed',
                'true'
            );

            statusLine.textContent =
                'Speak now. Your selected agent will not change.';
        };

        recognition.onresult = event => {
            if (!acceptResults || busy) {
                return;
            }

            const transcript = Array.from(
                event.results
            )
                .map(result => {
                    return result[0]?.transcript || '';
                })
                .join(' ')
                .trim();

            if (!transcript) {
                return;
            }

            received = true;

            input.value = [
                original,
                transcript
            ]
                .filter(Boolean)
                .join(' ');

            input.dispatchEvent(
                new Event('input', {
                    bubbles: true
                })
            );

            // Do not set agentSelect.value here.

            statusLine.textContent =
                'Review your text, then press Send.';
        };

        recognition.onerror = event => {
            if (
                !acceptResults &&
                event.error === 'aborted'
            ) {
                return;
            }

            failed = true;

            const errors = {
                'not-allowed':
                    'Allow microphone access in browser and Windows settings.',

                'service-not-allowed':
                    'Browser speech service is blocked.',

                'audio-capture':
                    'No working microphone found. Check Windows Sound settings.',

                'no-speech':
                    'No speech detected. Check your microphone.',

                'network':
                    'Browser speech service could not connect. Check internet/VPN/firewall.',

                'language-not-supported':
                    'Try another recognition language.',

                'aborted':
                    'Voice input stopped.'
            };

            if (!busy) {
                statusLine.textContent =
                    (
                        errors[event.error] ||
                        'Voice failed.'
                    ) +
                    ' [Voice error: ' +
                    event.error +
                    ']';
            }
        };

        recognition.onend = () => {
            acceptResults = false;

            resetVoice();

            if (busy || failed) {
                return;
            }

            statusLine.textContent = received
                ? 'Review the message and press Send.'
                : 'No words received. Check your microphone and selected language.';
        };

        window.addEventListener('pagehide', () => {
            stopVoiceInput();

            if ('speechSynthesis' in window) {
                window.speechSynthesis.cancel();
            }
        });
    }
}