'use strict';

const toggle = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#primary-nav');

if (toggle && navigation) {
    toggle.addEventListener('click', () => {
        const opened = navigation.classList.toggle('open');
        toggle.setAttribute('aria-expanded', String(opened));
    });
}

document.querySelectorAll('.confirm-action').forEach((form) => {
    form.addEventListener('submit', (event) => {
        if (!window.confirm(form.dataset.confirm)) {
            event.preventDefault();
        }
    });
});

document.querySelector('.print-button')?.addEventListener('click', () => {
    window.print();
});
