import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .agents import (
    AGENTS,
    AgentError,
    has_staff_access,
    route_agent,
    run_local,
    run_openai,
)

from .models import AgentUsage
from .autonomous import CUSTOMER_AGENTS, ADMIN_AGENTS
from .agent_runs import idempotent_chat
from .customer_workflow import customer_workflow

from .commerce_agents import (
    REQUEST_CONTEXT,
    STAFF_AGENTS,
)

from .order_cancellation import (
    cancellation_intent,
    cancellation_reply,
)


@login_required
def assistant_page(request):
    return render(
        request,
        "shop/assistant.html",
        {
            "ai_mode": settings.AI_MODE,
            "can_use_admin_agent": has_staff_access(
                request.user
            ),
            "agent_options": [
                (key, spec.name)
                for key, spec in AGENTS.items()
                if key in (CUSTOMER_AGENTS + (ADMIN_AGENTS if has_staff_access(request.user) else ()))
            ],
        },
    )


def consume_quota(user):
    hour = timezone.now().replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    with transaction.atomic():
        usage, _ = AgentUsage.objects.get_or_create(
            user=user,
            hour=hour,
        )

        return (
            AgentUsage.objects.filter(
                pk=usage.pk,
                requests__lt=settings.AI_MAX_REQUESTS_PER_HOUR,
            ).update(
                requests=F("requests") + 1
            )
            == 1
        )


@login_required
@require_POST
@idempotent_chat
def assistant_chat(request):
    if len(request.body) > 8000:
        return JsonResponse(
            {"error": "Message is too large."},
            status=400,
        )

    try:
        data = json.loads(request.body)

        if not isinstance(data, dict):
            raise ValueError

        message = data.get("message", "")

        if (
            not isinstance(message, str)
            or not 1 <= len(message.strip()) <= 1200
        ):
            raise ValueError

        message = message.strip()

        selected_agent = data.get(
            "agent",
            "auto",
        )

        agent_id = route_agent(
            message,
            selected_agent,
            request.user,
        )

        supports_cancellation = (
            agent_id in {
                "support",
                "payments",
                "voice",
            }
            or selected_agent == "auto"
        )

        if (
            supports_cancellation
            and cancellation_intent(message)
        ):
            agent_id = "support"

    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return JsonResponse(
            {
                "error": (
                    "Enter a message between "
                    "1 and 1,200 characters."
                )
            },
            status=400,
        )

    except AgentError as exc:
        return JsonResponse(
            {"error": str(exc)},
            status=403,
        )

    if not consume_quota(request.user):
        return JsonResponse(
            {
                "error": (
                    "Your hourly assistant limit "
                    "has been reached. "
                    "Please try again next hour."
                )
            },
            status=429,
        )

    histories = request.session.get(
        "agent_history",
        {},
    )

    history = histories.get(
        agent_id,
        [],
    )

    token = REQUEST_CONTEXT.set(
        {
            "request": request,
            "agent_id": agent_id,
            "routine_updates_authorized": data.get("authorize_routine_updates") is True,
            "response_language": data.get("language") if data.get("language") in ("en-IN", "ta-IN", "tanglish") else "auto",
        }
    )

    try:
        workflow = customer_workflow(
            request, message, agent_id, AGENTS[agent_id].tools,
            data.get("authorize_routine_updates") is True,
        )
        cancellation = None

        if agent_id == "support":
            cancellation = cancellation_reply(
                request,
                message,
            )

        if workflow is not None:
            result = workflow

        elif cancellation is not None:
            result = cancellation

        elif (
            settings.AI_MODE == "gemini"
            or (
                settings.AI_MODE != "local"
                and settings.AGENT_ENGINE == "langgraph"
            )
        ):
            from .llm_graph import run_graph

            result = run_graph(
                message,
                agent_id,
                request.user,
                history,
            )

        elif settings.AI_MODE == "openai":
            result = run_openai(
                message,
                agent_id,
                request.user,
                history,
            )

        else:
            result = run_local(
                message,
                agent_id,
                request.user,
            )

    except AgentError as exc:
        return JsonResponse(
            {"error": str(exc)},
            status=503,
        )

    finally:
        REQUEST_CONTEXT.reset(token)

    histories[agent_id] = (
        history
        + [
            {
                "role": "user",
                "content": message,
            },
            {
                "role": "assistant",
                "content": result["answer"][:3000],
            },
        ]
    )[-6:]

    request.session["agent_history"] = histories

    return JsonResponse(
        {
            **result,
            "agent": AGENTS[agent_id].name,
            "mode": settings.AI_MODE,
        }
    )


@login_required
@require_POST
def clear_chat(request):
    request.session.pop(
        "agent_history",
        None,
    )

    return JsonResponse(
        {"ok": True}
    )