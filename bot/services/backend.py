"""Trusted in-process backend access. No public bot API or extra HTTP service."""

import asyncio

from app.extensions import db
from app.models import Server
from app.services.authorization import authorize_group
from app.services.report_generator import report
from app.services.swap_manager import acknowledge, request_swap


class Backend:
    def __init__(self, app):
        self.app = app

    async def run(self, function, *args):
        def invoke():
            with self.app.app_context():
                try:
                    return function(*args)
                finally:
                    db.session.remove()

        return await asyncio.to_thread(invoke)

    async def bind(self, code, group_id, user_id):
        def bind():
            return authorize_group(code, group_id, user_id).name

        return await self.run(bind)

    async def reports(self, group_id, refresh=False):
        def get_reports():
            servers = db.session.scalars(
                db.select(Server)
                .where(Server.telegram_group_id == group_id, Server.is_authorized.is_(True))
                .order_by(Server.id)
                .limit(20)
            ).all()
            if refresh:
                # Public /report queues a fresh check; /status returns the most recent sample.
                from datetime import timedelta

                from app.models import utcnow
                from app.services.security import blocked, failed, rate_key

                key = rate_key("manual-report", group_id)
                if blocked(key):
                    raise ValueError("Зачекайте перед повторним запитом звіту.")
                failed(key)
                for server in servers:
                    server.next_check_at = utcnow()
                    server.next_report_at = utcnow() - timedelta(seconds=1)
                db.session.commit()
            return [report(server) for server in servers]

        return await self.run(get_reports)

    async def callback(self, operation, alert_id, group_id, user_id):
        action = acknowledge if operation == "ack" else request_swap
        return await self.run(action, alert_id, group_id, user_id)
