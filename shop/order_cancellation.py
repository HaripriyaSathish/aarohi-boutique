"""Customer-owned order lookup and cancellation proposals."""

import re
import unicodedata

from .models import Order
from .agents import serialize_order
from .actions import propose
from .services import ShopError


def normalise(value):
    value = unicodedata.normalize(
        "NFKC",
        value,
    ).casefold()

    return re.sub(
        r"[^\w\s]",
        " ",
        value,
        flags=re.UNICODE,
    ).strip()


def cancellation_intent(message):
    text = message.casefold()

    negative = (
        r"\b(don.?t|do not|never|cannot|can.t)\b"
        r"|வேண்டாம்|வேணாம்|பண்ணாத|செய்யாத|முடியாது"
    )

    if re.search(negative, text):
        return False

    return bool(
        re.search(
            r"\bcancel(?:lation|led)?\b"
            r"|கேன்சல்|கான்சல்|ரத்து",
            text,
        )
    )


def cancellation_reply(request, message):
    if not cancellation_intent(message):
        return None

    tamil = bool(
        re.search(
            r"[\u0B80-\u0BFF]",
            message,
        )
    )

    result = {
        "answer": "",
        "products": [],
        "orders": [],
        "actions": [],
        "tools": ["find_my_orders"],
        "engine": "Verified order workflow",
    }

    def answer(english, tamil_text):
        result["answer"] = (
            tamil_text if tamil else english
        )

        return result

    orders = (
        Order.objects
        .filter(user=request.user)
        .order_by("-created")
        .prefetch_related("items", "events")
    )

    references = re.findall(
        r"\bAB-[A-Fa-f0-9]{8}\b",
        message,
        re.I,
    )

    matches = []

    if references:
        wanted = {
            reference.upper()
            for reference in references
        }

        matches = [
            order
            for order in orders
            if order.number in wanted
        ]

        if len(matches) != len(wanted):
            return answer(
                "That order reference was not found "
                "in your account.",

                "அந்த order number உங்க account-ல் "
                "கிடைக்கவில்லை.",
            )

    else:
        text = (
            " "
            + " ".join(normalise(message).split())
            + " "
        )

        names = set(
            orders.values_list(
                "items__name",
                flat=True,
            )
        ) - {None, ""}

        found = [
            name
            for name in names
            if (
                " "
                + " ".join(normalise(name).split())
                + " "
            ) in text
        ]

        found = [
            name
            for name in found
            if not any(
                name != other
                and normalise(name) in normalise(other)
                for other in found
            )
        ]

        if len(found) == 1:
            matches = list(
                orders.filter(
                    items__name=found[0]
                ).distinct()
            )

        else:
            recent = list(orders[:8])

            result["orders"] = [
                serialize_order(order)
                for order in recent
            ]

            if not recent:
                return answer(
                    "You have no orders in this account. "
                    "Nothing was cancelled.",

                    "உங்க account-ல் orders இல்லை. "
                    "எதுவும் cancel செய்யப்படவில்லை.",
                )

            return answer(
                "Please identify the order: send "
                "'Cancel AB-XXXXXXXX' using your order "
                "number, or 'Cancel <exact purchased "
                "product name> order'. Your recent "
                "orders are shown below. No order "
                "has been cancelled.",

                "கீழே உங்க recent orders இருக்கு. "
                "'Cancel AB-XXXXXXXX' என்று order "
                "number சொல்லுங்க; அல்லது வாங்கிய "
                "exact product name-உடன் cancel கேளுங்க. "
                "எந்த order-யும் இன்னும் cancel செய்யவில்லை.",
            )

    result["orders"] = [
        serialize_order(order)
        for order in matches[:8]
    ]

    if len(matches) != 1:
        return answer(
            "More than one order matches. Choose the "
            "correct order below and send "
            "'Cancel <order number>'. "
            "Nothing has been cancelled.",

            "ஒன்றுக்கும் மேற்பட்ட orders match ஆகுது. "
            "சரியான order number-உடன் cancel கேளுங்க. "
            "எதுவும் இன்னும் cancel செய்யவில்லை.",
        )

    order = matches[0]

    if order.status not in ["pending", "confirmed"]:
        return answer(
            f"{order.number} cannot be cancelled at "
            f"its current status "
            f"({order.get_status_display()}). "
            "Check its order page for eligible return "
            "options. No cancellation was made.",

            f"{order.number} இப்போதைய status "
            f"({order.get_status_display()})-ல் "
            "cancel செய்ய முடியாது. Eligible return "
            "options-ஐ order page-ல் பாருங்க.",
        )

    partial_words = [
        "only",
        "மட்டும்",
        "one item",
        "one unit",
    ]

    if (
        order.items.count() != 1
        or any(
            word in message.casefold()
            for word in partial_words
        )
    ):
        return answer(
            "This order contains multiple items or "
            "you requested a partial cancellation. "
            "This project supports whole-order "
            "cancellation only. Open the order page "
            "and review all items before choosing "
            "Cancel order. Nothing has been cancelled.",

            "இந்த order-ல் பல items இருக்கு அல்லது "
            "நீங்க ஒரு item மட்டும் cancel கேட்கிறீங்க. "
            "இந்த project-ல் முழு order cancellation "
            "மட்டுமே இருக்கு. Order page-ல் எல்லா "
            "items-யும் பார்த்த பிறகு Cancel order "
            "தேர்வு செய்யுங்க. இன்னும் எதுவும் "
            "cancel செய்யவில்லை.",
        )

    try:
        card = propose(
            request,
            "cancel_order",
            {
                "target": str(order.pk),
                "value": "Customer requested cancellation",
                "quantity": 1,
                "extra": "",
            },
            agent="support",
        )

    except ShopError:
        return answer(
            "The order changed or is no longer "
            "eligible. Refresh its order page. "
            "Nothing was cancelled.",

            "Order status மாறியிருக்கலாம். "
            "Order page refresh செய்து பாருங்க. "
            "எதுவும் cancel செய்யவில்லை.",
        )

    result["actions"] = [card]

    result["tools"].append("propose_action")

    return answer(
        f"I found {order.number}. Review the "
        "cancellation card and press "
        "'Confirm this exact action'. "
        "This cancels the entire order. "
        "No cancellation or refund has been "
        "executed yet; paid cancellations "
        "require refund processing.",

        f"{order.number} கிடைத்தது. கீழே cancellation "
        "card-ஐ பார்த்து 'Confirm this exact action' "
        "அழுத்துங்க. முழு order cancel ஆகும். "
        "இன்னும் cancellation/refund செய்யவில்லை; "
        "paid order refund தனியாக process ஆகணும்.",
    )