"""Client retry protection. Uncertain crashed runs are never silently replayed."""
import hashlib
import json
import uuid
from functools import wraps
from django.http import JsonResponse
from .autonomous import AgentRun


def idempotent_chat(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        key = request.headers.get('Idempotency-Key')
        if not key:
            return view(request, *args, **kwargs)
        try:
            key = uuid.UUID(key)
        except (ValueError, TypeError):
            return JsonResponse({'error':'Invalid request identifier.'},status=400)
        if len(request.body)>8000:
            return JsonResponse({'error':'Message is too large.'},status=400)
        fingerprint = hashlib.sha256(request.body).hexdigest()
        run, created = AgentRun.objects.get_or_create(user=request.user,key=key,defaults={'fingerprint':fingerprint})
        if not created:
            if run.fingerprint != fingerprint:
                return JsonResponse({'error':'This request identifier belongs to another message.'},status=409)
            if run.state == 'running':
                return JsonResponse({'error':'This request is processing or needs reconciliation. Check your bag, orders and action history before starting another request.'},status=409)
            return JsonResponse(run.response,status=run.status_code)
        try:
            response = view(request, *args, **kwargs)
            run.response = json.loads(response.content)
            run.status_code = response.status_code
            run.state = 'completed' if response.status_code<400 else 'failed'
            run.save()
            return response
        except Exception:
            # Keep an uncertain state, rather than replaying possible external effects.
            raise
    return wrapped
