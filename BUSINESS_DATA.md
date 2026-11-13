# Data needed by business agents

The initial seed creates a sample catalogue only. Reports do not turn nonexistent sales, supplier performance or costs into plausible-looking numbers.

## Costs and financial measures

Enter actual `Product.cost_price` in admin. New order lines snapshot that cost. Unknown historic costs remain unknown; changing a product cost does not rewrite old order margins. The report shows collected cash, processed refunds, cost coverage and a limited cash-less-known-cost estimate. Tax, shipping costs, overhead and pending refunds require actual accounting data; this is not a profit ledger or GST invoice engine.

## Demand

The baseline observes up to 56 calendar days of order-line demand. Fewer than 28 days of observed history or 5 units produces `insufficient_data`. The method is an EWMA (alpha 0.2), with a last-seven-day daily absolute error measure. It is a transparent starting model, not a validated guarantee of future sales or stockout avoidance.

## Suppliers and purchase orders

Create Suppliers and Supplier Quotes in admin. Quotes contain SKU, unit cost, MOQ, freight, lead time and validity. Performance measures use actually received purchase orders and recorded defect/receipt dates. Missing history is not treated as perfect performance.

The agent can prepare a draft internal purchase order, approve it and receive its inspected full quantity through guarded actions. This does not send an external supplier order or make a supplier payment. Receiving a purchase order restocks saleable units once and closes overlapping restock plans to reduce double-entry risk. Supplier purchasing/settlement APIs were not provided.

CSV import:

```bash
python manage.py import_business_csv suppliers your_quotes.csv
python manage.py import_business_csv competitors your_observations.csv
```

Use headers in `data_examples/`. Matching catalogue SKU/product slug is required. Imports roll back as a whole on invalid data. Re-importing appends observations/quotes; the command is not a periodic scraper or a deduplicating sync.

## Competitors and pricing

Enter comparable observations with seller, price, product and source URL. The report uses observations from the last 30 days and compares their median with the cost-based floor. It does not browse/scrape arbitrary competitor sites or guarantee an optimum price. Price changes require review and never rewrite existing order totals.

## Customer segments and growth

Segments are based on actual account/order records. Inactive means no recent recorded order activity after the 90-day threshold; this is a segment definition, not a prediction of why someone left. Campaign audience approval snapshots IDs, then consent is checked again. Changed campaign copy invalidates an old approval. No ads account is connected and no advertising budget is spent.

## Risk and returns

Risk flags use explainable order velocity, shared delivery phone and order-value/account-age rules. Families can share phone numbers and genuine buyers can place multiple orders. Flags create review/fulfillment holds; they do not prove fraud or auto-ban accounts. A human can review and release a hold. Return history/reasons similarly support review; physical receipt and inspection remain required.

## Logistics and warehouses

Add a Warehouse with actual coordinates and fill delivery coordinates on relevant orders in admin if planning your own local route. Local route order is a nearest-neighbour straight-line heuristic; it excludes roads, traffic, driver shifts and capacity constraints. Third-party courier routing is not controlled by this app.

Global Variant.stock remains the source of inventory truth. When regional warehouse/city demand exists, inventory reports can suggest a proportional allocation of global available units. These are plans, not an implemented per-warehouse inventory/transfer ledger.
