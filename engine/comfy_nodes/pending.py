# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Atomic removal receipt for an owned, never-started Comfy prompt."""

def cancel_pending(queue,prompt_id,client_id):
    if not isinstance(prompt_id,str) or not prompt_id or not isinstance(client_id,str) or len(client_id)!=32:
        raise ValueError('A prompt ID and its owning client ID are required')
    # The public queue method tests/removes under its queue mutex. A dequeued job
    # cannot match here and is left to drain to terminal history.
    removed=queue.delete_queue_item(lambda row:isinstance(row,(tuple,list)) and len(row)>3 and
        row[1]==prompt_id and isinstance(row[3],dict) and row[3].get('client_id')==client_id)
    return {'promptId':prompt_id,'clientId':client_id,'cancelledBeforeStart':removed is True}

def register():
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    @PromptServer.instance.routes.post('/sprintwake/cancel-pending')
    async def cancel(request):
        if request.remote not in {'127.0.0.1','::1'}:
            return web.json_response({'error':'Owned cancellation is loopback-only'},status=403)
        try:
            body=await request.json()
            return web.json_response(cancel_pending(PromptServer.instance.prompt_queue,body.get('promptId'),body.get('clientId')))
        except (ValueError,TypeError,AttributeError):
            return web.json_response({'error':'Invalid owned cancellation request'},status=400)
