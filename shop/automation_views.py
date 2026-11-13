import json
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from .models import (
    AgentAction,
    AgentJob,
    AgentAudit,
    WorkTask,
    RestockPlan,
    Shipment,
    Notification,
    CustomerPreference,
    AutomationPolicy,
    ReturnRequest,
)
from .agents import has_staff_access
from .actions import propose, apply_action, STAFF_PERMISSIONS
from .automation import policy, sweep, run_jobs, audit
from .services import ShopError


class PolicyForm(forms.ModelForm):
    class Meta:
        model = AutomationPolicy
        fields = [
            "enabled",
            "auto_shipping",
            "auto_return_approval",
            "auto_refunds",
            "fraud_review_threshold",
            "allow_live_refunds",
            "low_stock_threshold",
            "restock_target",
            "review_delay_days",
            "abandoned_cart_hours",
            "auto_cart_reminders",
        ]

    def clean(self):
        data = super().clean()
        if data.get("restock_target", 0) < data.get("low_stock_threshold", 0):
            raise forms.ValidationError(
                "Restock target must be at least the low-stock threshold."
            )
        return data


def staff_required(request):
    if not has_staff_access(request.user):
        raise PermissionDenied


@login_required
def operations(request):
    staff_required(request)
    can_edit = request.user.has_perm("shop.change_automationpolicy")
    form = PolicyForm(request.POST or None, instance=policy())
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            audit(
                "Admin Operations",
                "policy_updated",
                "Store automation policy updated by authorized staff.",
                actor=request.user,
            )
            messages.success(
                request,
                "Automation settings saved. Live refunds follow your selected policy.",
            )
            return redirect("operations")
    return render(
        request,
        "shop/operations.html",
        {
            "jobs": AgentJob.objects.select_related("order")[:40],
            "review_jobs": AgentJob.objects.filter(state__in=["review", "failed"])[:20],
            "tasks": WorkTask.objects.filter(status="open").select_related(
                "order", "return_request"
            )[:30],
            "pending_returns": ReturnRequest.objects.filter(
                status="requested"
            ).select_related("item__order")[:20],
            "plans": RestockPlan.objects.filter(status="proposed").select_related(
                "variant__product"
            )[:20],
            "shipments": Shipment.objects.select_related("order").order_by("-created")[
                :20
            ],
            "audits": AgentAudit.objects.all()[:30],
            "actions": AgentAction.objects.filter(user=request.user, state="pending")[
                :20
            ],
            "form": form,
            "can_edit_policy": can_edit,
        },
    )


@login_required
@require_POST
def run_cycle(request):
    staff_required(request)
    if not request.user.has_perm("shop.change_agentjob"):
        raise PermissionDenied
    sweep()
    count = run_jobs(limit=30)
    messages.success(
        request,
        f"Processed {count} automation jobs. Run the background worker for continuous automation.",
    )
    return redirect("operations")


@login_required
@require_POST
def propose_action(request):
    try:
        result = propose(
            request,
            request.POST.get("kind", ""),
            {
                key: request.POST.get(key, "")
                for key in ["target", "value", "extra", "quantity"]
            },
            agent="Website action assistant",
        )
        return redirect("action_review", pk=result["id"])
    except (ShopError, ValueError, TypeError) as exc:
        messages.error(request, str(exc))
        return redirect("operations" if request.user.is_staff else "account")


@login_required
def action_review(request, pk):
    action = get_object_or_404(AgentAction, pk=pk, user=request.user)
    return render(request, "shop/action_review.html", {"action": action})


@login_required
@require_POST
def confirm_action(request, pk):
    action = get_object_or_404(AgentAction, pk=pk, user=request.user)
    try:
        result = apply_action(request, action.pk)
    except ShopError as exc:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect("action_review", pk=pk)
    if request.headers.get("Accept") == "application/json":
        return JsonResponse(result)
    messages.success(request, result["message"])
    return redirect("operations" if request.user.is_staff else result["url"])


@login_required
@require_POST
def reject_action(request, pk):
    AgentAction.objects.filter(pk=pk, user=request.user, state="pending").update(
        state="rejected"
    )
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"ok": True})
    return redirect("operations" if request.user.is_staff else "account")


@login_required
def notifications(request):
    preferences, _ = CustomerPreference.objects.get_or_create(user=request.user)
    if request.method == "POST":
        if request.POST.get("save_preferences"):
            preferences.email_updates = "email_updates" in request.POST
            preferences.marketing_opt_in = "marketing_opt_in" in request.POST
            preferences.review_requests = "review_requests" in request.POST
            preferences.save()
            messages.success(request, "Notification preferences saved.")
        elif request.POST.get("read"):
            Notification.objects.filter(
                user=request.user, pk=request.POST["read"]
            ).update(read=True)
        return redirect("notifications")
    return render(
        request,
        "shop/notifications.html",
        {
            "notifications": Notification.objects.filter(user=request.user)[:50],
            "preferences": preferences,
        },
    )


@login_required
def pending_board(request):
    """Admin Pending-Work board: every queue waiting for staff, prioritised."""
    staff_required(request)
    from .pending_work import collect

    data = collect()
    return render(request, "shop/pending_work.html", {"data": data})


@login_required
def pending_briefing(request):
    staff_required(request)
    from .pending_work import briefing

    text, engine = briefing()
    return JsonResponse({"briefing": text, "engine": engine})
