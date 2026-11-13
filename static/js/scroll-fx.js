/* Aarohi scroll effects
 * 1. Light smooth scroll for mouse wheels (desktop only; touch, keyboard and
 *    scrollbars stay native; nested scroll areas like the AI chat are untouched).
 * 2. Scroll-reveal animations for sections, headings, cards and images on every page.
 * 3. Subtle parallax on hero / banner images.
 * 4. Spotlight hover highlight that follows the cursor on cards.
 * Everything switches off for prefers-reduced-motion.
 */
(() => {
    'use strict';
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    const root = document.documentElement;

    /* ---------- 1. Light smooth wheel scroll ---------- */
    function isScrollable(el, deltaY) {
        while (el && el !== document.body && el !== root) {
            if (el.matches?.('textarea, select, [data-native-scroll], .aarohi-ai-panel, dialog')) return true;
            const style = getComputedStyle(el);
            const canY = /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
            if (canY) {
                const atTop = el.scrollTop <= 0, atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;
                if (!((deltaY < 0 && atTop) || (deltaY > 0 && atBottom))) return true;
            }
            el = el.parentElement;
        }
        return false;
    }

    if (!reduce && finePointer) {
        let target = window.scrollY, current = window.scrollY, running = false;
        const ease = 0.12;                       // lower = floatier, higher = snappier
        const maxY = () => root.scrollHeight - window.innerHeight;
        root.classList.add('fx-smooth');        // disable CSS smooth-scroll so it doesn't fight the JS easing

        function step() {
            current += (target - current) * ease;
            if (Math.abs(target - current) < 0.5) {
                current = target;
                running = false;
            }
            window.scrollTo({ top: current, behavior: 'instant' });
            if (running) requestAnimationFrame(step);
        }

        window.addEventListener('wheel', (event) => {
            if (event.ctrlKey || event.defaultPrevented || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
            if (isScrollable(event.target, event.deltaY)) return;
            event.preventDefault();
            if (!running) { target = current = window.scrollY; }
            const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? window.innerHeight : 1;
            target = Math.max(0, Math.min(maxY(), target + event.deltaY * unit * 0.9));
            if (!running) { running = true; requestAnimationFrame(step); }
        }, { passive: false });

        // Keep in sync when something else scrolls (keyboard, links, scrollbar drag).
        window.addEventListener('scroll', () => { if (!running) target = current = window.scrollY; }, { passive: true });
    }

    /* ---------- 2. Scroll reveal ---------- */
    const groups = [
        // [selector, effect]
        ['main h1, main .page-heading, main .section-heading, main .ah-heading, main .pw-head', 'up'],
        ['main .eyebrow, main .ah-kicker', 'fade'],
        ['main .panel, main .order-card, main .bag-item, main .pw-card, main .pw-stat, main .pw-briefing, main .agent-hub-card, main .agent-choice, main .info-card, main article.card', 'up'],
        ['main .ah-category, main .category-card', 'zoom'],
        ['main .ah-banner:nth-child(odd), main .ah-stylist-art', 'left'],
        ['main .ah-banner:nth-child(even), main .ah-stylist-copy', 'right'],
        ['main .ah-featured-band, main .ah-hero-bottom, main .empty-state', 'up'],
        ['main table, main form:not(.card-wishlist):not(.inline-form)', 'fade'],
        ['.af-trust-item, .af-hero > *, .af-grid > *, .af-bottom-row', 'up'],
    ];
    if (!reduce && 'IntersectionObserver' in window) {
        root.classList.add('fx-ready');
        const io = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('fx-in');
                    io.unobserve(entry.target);
                }
            });
        }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });

        const seen = new Set();
        groups.forEach(([selector, effect]) => {
            document.querySelectorAll(selector).forEach((el) => {
                if (seen.has(el) || el.classList.contains('reveal') || el.closest('.aarohi-ai-shell, dialog, .site-header')) return;
                // Skip things nested inside an element that is already animating.
                if ([...seen].some((parent) => parent.contains(el))) return;
                seen.add(el);
                const siblings = el.parentElement ? [...el.parentElement.children].indexOf(el) : 0;
                el.classList.add('fx', 'fx-' + effect);
                el.style.setProperty('--fx-delay', ((siblings % 6) * 70) + 'ms');
                // Already on screen at load: show immediately (no flash of hidden content).
                const r = el.getBoundingClientRect();
                if (r.top < window.innerHeight * 0.9 && r.bottom > 0) {
                    setTimeout(() => el.classList.add('fx-in'), 40);   // timer, not rAF: works in background tabs too
                } else {
                    io.observe(el);
                }
            });
        });
    }

    // Safety net: never leave content hidden if something goes wrong.
    setTimeout(() => document.querySelectorAll('.fx:not(.fx-in)').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.top < window.innerHeight && r.bottom > 0) el.classList.add('fx-in');
    }), 2500);

    /* ---------- 3. Subtle parallax ---------- */
    const parallax = reduce ? [] : [...document.querySelectorAll('.ah-hero-visual img, .ah-banner-art img, .ah-stylist-art img')];
    if (parallax.length) {
        let ticking = false;
        const update = () => {
            const vh = window.innerHeight;
            parallax.forEach((img) => {
                const box = img.parentElement.getBoundingClientRect();
                if (box.bottom < 0 || box.top > vh) return;
                const progress = (box.top + box.height / 2 - vh / 2) / vh;   // -1 .. 1
                img.style.setProperty('--parallax', (progress * -28).toFixed(1) + 'px');
            });
            ticking = false;
        };
        window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
        update();
    }

    /* ---------- 4. Spotlight hover on cards ---------- */
    if (finePointer && !reduce) {
        const cardSelector = '.product-card, .panel, .order-card, .pw-card, .pw-stat, .agent-hub-card, .ah-category, .bag-item, .af-trust-item, .aarohi-ai-card';
        document.addEventListener('pointermove', (event) => {
            const card = event.target.closest?.(cardSelector);
            if (!card) return;
            const r = card.getBoundingClientRect();
            card.style.setProperty('--mx', ((event.clientX - r.left) / r.width * 100).toFixed(1) + '%');
            card.style.setProperty('--my', ((event.clientY - r.top) / r.height * 100).toFixed(1) + '%');
        }, { passive: true });
    }

    /* ---------- Header: compact on scroll ---------- */
    const header = document.querySelector('.site-header');
    if (header) {
        const onScroll = () => header.classList.toggle('is-compact', window.scrollY > 120);
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
    }
})();
