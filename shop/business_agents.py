"""The requested business capabilities, grounded in recorded store data."""

from .intelligence import (
    business_metrics,
    forecast_demand,
    supplier_comparison,
    pricing_intelligence,
    inventory_optimization,
    marketing_insights,
    fraud_report,
    return_investigation,
    logistics_analysis,
    management_plan,
)

REPORTS = {
    "business_metrics": business_metrics,
    "demand_forecast": forecast_demand,
    "supplier_comparison": supplier_comparison,
    "pricing_intelligence": pricing_intelligence,
    "inventory_optimization": inventory_optimization,
    "marketing_strategy": marketing_insights,
    "retention_insights": marketing_insights,
    "fraud_investigation": fraud_report,
    "return_investigation": return_investigation,
    "logistics_optimization": logistics_analysis,
    "business_manager": management_plan,
}


def install(module):
    from .commerce_agents import STAFF_AGENTS, ACTION_SCOPES

    Spec = module.AgentSpec
    for name in REPORTS:
        module.TOOL_SCHEMAS[name] = module.function_schema(
            name,
            "Read measured business records and explicitly labelled estimates. Missing data is reported; no invented metrics.",
            {},
        )
    definitions = {
        "business": ("Business Intelligence Agent", "business_metrics", set()),
        "forecast": ("Demand Forecasting Agent", "demand_forecast", set()),
        "suppliers": (
            "Supplier Intelligence Agent",
            "supplier_comparison",
            {
                "create_purchase_order",
                "approve_purchase_order",
                "receive_purchase_order",
            },
        ),
        "pricing": (
            "Pricing Intelligence Agent",
            "pricing_intelligence",
            {"update_price"},
        ),
        "inventory_optimizer": (
            "Inventory Optimization Agent",
            "inventory_optimization",
            {"receive_stock"},
        ),
        "marketing": (
            "Marketing Strategy Agent",
            "marketing_strategy",
            {"draft_campaign", "approve_campaign"},
        ),
        "retention": (
            "Customer Retention Agent",
            "retention_insights",
            {"draft_campaign"},
        ),
        "fraud": ("Fraud Investigation Agent", "fraud_investigation", {"release_hold"}),
        "return_investigator": (
            "Return Investigation Agent",
            "return_investigation",
            {"approve_return"},
        ),
        "logistics": (
            "Logistics Optimization Agent",
            "logistics_optimization",
            {"refresh_tracking"},
        ),
        "manager": (
            "Autonomous Business Manager",
            "business_manager",
            {"retry_job", "release_hold", "create_purchase_order", "draft_campaign"},
        ),
    }
    for key, (name, report, actions) in definitions.items():
        STAFF_AGENTS.add(key)
        ACTION_SCOPES[key] = actions
        tools = (report, "propose_action") if actions else (report,)
        module.AGENTS[key] = Spec(
            name,
            "Analyze the report, distinguish measured facts from estimates, and give practical next steps. Never manufacture supplier, competitor, cost, demand or route data. Propose changes only when the user asks. Financial/campaign/catalogue actions need confirmation.",
            tools,
        )
    ACTION_SCOPES["content"].add("save_seo")
    ACTION_SCOPES["voice"] = {"add_to_cart", "remove_from_cart", "apply_coupon"}
    ACTION_SCOPES["recovery"] = {"apply_coupon"}
    module.AGENTS["voice"] = Spec(
        "Voice Shopping Agent",
        "The browser supplies an editable voice transcript. Search and compare actual products and prepare requested cart edits. Never collect payment credentials or claim a transcript is perfectly accurate.",
        ("search_products", "get_product_details", "cart_status", "propose_action"),
    )
    module.AGENTS["recovery"] = Spec(
        "Cart Recovery Agent",
        "Inspect this customer’s current cart and eligible coupons. Explain observed stock/totals without inventing reasons for abandonment. Suggest permitted existing offers only.",
        ("cart_status", "best_coupon", "propose_action"),
    )
    all_actions = sorted(set().union(*ACTION_SCOPES.values()))
    module.TOOL_SCHEMAS["propose_action"]["parameters"]["properties"]["action"][
        "enum"
    ] = all_actions
