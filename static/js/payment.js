'use strict';

const payButton = document.querySelector('#pay-button');
const config = document.querySelector('#gateway-config');
const message = document.querySelector('#payment-message');

payButton?.addEventListener('click', () => {
    if (typeof window.Razorpay !== 'function') {
        message.textContent = 'Gateway could not load. Check your connection and retry.';
        return;
    }
    payButton.disabled = true;
    const checkout = new window.Razorpay({
        key: config.dataset.key,
        order_id: config.dataset.order,
        currency: 'INR',
        name: config.dataset.name,
        description: 'Boutique order',
        prefill: {
            email: config.dataset.email,
            contact: config.dataset.phone,
        },
        theme: {
            color: '#76549b',
        },
        handler: (response) => {
            const form = document.querySelector('#payment-result');
            ['razorpay_payment_id', 'razorpay_order_id', 'razorpay_signature'].forEach((name) => {
                form.elements[name].value = response[name];
            });
            message.textContent = 'Verifying your payment. Please wait…';
            form.submit();
        },
        modal: {
            ondismiss: () => {
                payButton.disabled = false;
                message.textContent = 'Checkout closed. If money was debited, check your order status before retrying.';
            },
        },
    });
    checkout.on('payment.failed', () => {
        payButton.disabled = false;
        message.textContent = 'Payment did not complete. If your bank debited money, check the order status before retrying.';
    });
    checkout.open();
});

const cfButton = document.querySelector('#cashfree-pay');
const cfConfig = document.querySelector('#cashfree-config');
cfButton?.addEventListener('click', () => {
    const msg = document.querySelector('#payment-message');
    if (typeof window.Cashfree !== 'function') {
        msg.textContent = 'Cashfree could not load. Check your connection and retry.';
        return;
    }
    cfButton.disabled = true;
    msg.textContent = 'Opening secure Cashfree checkout…';
    const cashfree = window.Cashfree({mode: cfConfig.dataset.mode});
    cashfree.checkout({paymentSessionId: cfConfig.dataset.session, redirectTarget: '_self'})
        .then((result) => {
            if (result && result.error) {
                cfButton.disabled = false;
                msg.textContent = result.error.message || 'Payment did not complete. You can retry.';
            }
        });
});
