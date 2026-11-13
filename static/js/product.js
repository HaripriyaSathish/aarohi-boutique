'use strict';

document.querySelectorAll('.image-thumb').forEach((button) => {
    button.addEventListener('click', () => {
        const mainImage = document.querySelector('#main-product-image');
        if (mainImage) {
            mainImage.src = button.dataset.image;
        }
    });
});

const checkButton = document.querySelector('#check-delivery');
checkButton?.addEventListener('click', async () => {
    const postcode = document.querySelector('#delivery-pin').value;
    const result = document.querySelector('#delivery-result');
    checkButton.disabled = true;
    try {
        const response = await fetch('/delivery-check/?postcode=' + encodeURIComponent(postcode));
        const data = await response.json();
        result.textContent = data.message;
    } catch (error) {
        result.textContent = 'Unable to check right now. Please contact the store.';
    } finally {
        checkButton.disabled = false;
    }
});
