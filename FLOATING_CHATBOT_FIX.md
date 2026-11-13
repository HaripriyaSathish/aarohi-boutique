# Aarohi AI customer chat

Open the storefront and click **Ask Aarohi AI**. **Smart Assistant (Auto)** is selected by default. The six specialist options are available in the same panel. Sign in as a customer to use the chat.

## Try an automatic purchase

1. Use a customer account with a valid email and saved delivery address.
2. Pick an exact product name from the active catalogue. The product needs one eligible variant and enough stock.
3. Send `Buy 2 <product name>` or `Add 2 <product name> to my bag and prepare checkout`.
4. The server checks stock, adds the items to the bag and shows a checkout card. A request to add items alone updates the bag. Confirm the checkout to create the order, then authorize payment using the configured gateway.

The exact-name workflow runs locally as well as with a configured AI provider. Multiple eligible variants require a choice. An unavailable saved address blocks checkout preparation and leaves the bag unchanged. The standard assistant handles broader messages with the selected provider. Local mode provides a limited rules-based assistant.

## Upgrade and run

Keep your existing database and .env when upgrading, and keep a backup. In the directory containing manage.py, run:

    python manage.py migrate
    python manage.py runserver

For Gemini conversations, configure AI_MODE=gemini, GEMINI_API_KEY and GEMINI_MODEL, and run `python manage.py check_agents --live`. Do not share your API key. If your local demo database is empty, run `python manage.py seed_demo`.

To process configured background order jobs, run `python manage.py run_agents --watch` in a second terminal. Live payment, notifications and shipping require their provider settings.
