'use strict';

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const progress = document.querySelector('#scroll-progress');
const topButton = document.querySelector('#back-to-top');
let ticking = false;

function updateScroll() {
    const maximum = document.documentElement.scrollHeight - window.innerHeight;
    if (progress) {
        progress.style.width = (maximum > 0 ? window.scrollY / maximum * 100 : 0) + '%';
    }
    document.querySelector('.site-header')?.classList.toggle('scrolled', window.scrollY > 20);
    if (topButton) {
        topButton.hidden = window.scrollY < 500;
    }
    ticking = false;
}
window.addEventListener('scroll', () => {
    if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(updateScroll);
    }
}, {passive: true});
window.addEventListener('resize', updateScroll);
updateScroll();
topButton?.addEventListener('click', () => window.scrollTo({top: 0, behavior: reducedMotion ? 'auto' : 'smooth'}));

if (!reducedMotion && 'IntersectionObserver' in window) {
    document.documentElement.classList.add('motion-ready');
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, {threshold: 0.06});
    document.querySelectorAll('.section-heading, .category-card, .product-card, .collection-banner, .trust-strip > div').forEach((element, index) => {
        element.classList.add('reveal');
        element.style.setProperty('--reveal-delay', String((index % 4) * 55) + 'ms');
        observer.observe(element);
    });

}

const dialog = document.querySelector('#quick-view-dialog');
const content = document.querySelector('#quick-view-content');
let returnFocus = null;
let quickViewRequest = 0;

function closeQuickView() {
    dialog?.close();
    returnFocus?.focus();
}
document.querySelector('#quick-view-close')?.addEventListener('click', closeQuickView);
dialog?.addEventListener('click', (event) => {
    if (event.target === dialog) {
        const bounds = dialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
            closeQuickView();
        }
    }
});
dialog?.addEventListener('close', () => returnFocus?.focus());

document.querySelectorAll('[data-product-slug]').forEach((button) => {
    button.addEventListener('click', async () => {
        if (!dialog || typeof dialog.showModal !== 'function') {
            window.location.href = '/product/' + encodeURIComponent(button.dataset.productSlug) + '/';
            return;
        }
        returnFocus = button;
        const requestId = ++quickViewRequest;
        content.textContent = 'Loading your next favourite…';
        if (!dialog.open) {
            dialog.showModal();
        }
        try {
            const response = await fetch('/product-preview/' + encodeURIComponent(button.dataset.productSlug) + '/');
            if (!response.ok) {
                throw new Error('Product details are unavailable.');
            }
            const product = await response.json();
            if (requestId !== quickViewRequest) {
                return;
            }
            const layout = document.createElement('div');
            layout.className = 'quick-view-layout';
            const image = document.createElement('img');
            if (product.image?.startsWith('/media/')) {
                image.src = product.image;
            } else {
                image.src = '/static/images/placeholder.svg';
            }
            image.alt = product.name;
            const copy = document.createElement('div');
            const category = document.createElement('span');
            category.className = 'eyebrow';
            category.textContent = product.category;
            const title = document.createElement('h2');
            title.id = 'quick-view-title';
            title.textContent = product.name;
            const price = document.createElement('p');
            price.className = 'detail-price';
            price.textContent = '₹' + product.price_inr;
            const description = document.createElement('p');
            description.textContent = product.description;
            const sizes = document.createElement('div');
            sizes.className = 'quick-view-sizes';
            product.variants.forEach((variant) => {
                const tag = document.createElement('span');
                tag.textContent = variant.size + ' · ' + variant.colour + (variant.stock > 0 ? '' : ' · sold out');
                sizes.append(tag);
            });
            const open = document.createElement('a');
            open.className = 'button';
            open.href = product.url;
            open.textContent = 'View details & choose size →';
            copy.append(category, title, price, description, sizes, open);
            layout.append(image, copy);
            content.replaceChildren(layout);
        } catch (error) {
            content.textContent = error.message;
        }
    });
});

document.querySelectorAll('[data-marquee-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
        const track = button.closest('.fashion-marquee').querySelector('.marquee-track');
        const paused = button.getAttribute('aria-pressed') !== 'true';
        button.setAttribute('aria-pressed', String(paused));
        button.textContent = paused ? 'Play' : 'Pause';
        track.style.animationPlayState = paused ? 'paused' : 'running';
    });
});
