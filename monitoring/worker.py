import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update

from app import create_app
from app.extensions import db
from app.models import Job, Server, ServerLog, ServerMetric, utcnow
from app.services.alert_manager import connection_failed, enqueue, evaluate, setting, update_alert
from app.services.report_generator import report
from app.services.ssh_client import SSHFailure
from app.services.swap_manager import execute_swap

from .cleanup import aggregate_hour, cleanup
from .collectors import collect_server

logger = logging.getLogger(__name__)


def monitor_one(server_id):
    server = db.session.get(Server, server_id)
    if not server:
        return
    data = None
    failure = "SSH-з’єднання недоступне."
    for attempt in range(2):
        try:
            data = collect_server(server)
            break
        except SSHFailure as error:
            failure = str(error)
            if attempt == 0:
                time.sleep(2)
    now = utcnow()
    if data is None:
        connection_failed(server, failure)
    else:
        metric = ServerMetric(server_id=server.id, **data)
        db.session.add(metric)
        db.session.flush()
        for event in data["details"].get("errors", [])[:30]:
            stamp = datetime.fromtimestamp(event["timestamp"], UTC).replace(tzinfo=None)
            exists = db.session.scalar(
                db.select(ServerLog.id)
                .where(
                    ServerLog.server_id == server.id,
                    ServerLog.service_name == event["service"],
                    ServerLog.created_at == stamp,
                )
                .limit(1)
            )
            if not exists:
                db.session.add(
                    ServerLog(
                        server_id=server.id,
                        service_name=event["service"][:100],
                        log_level=event["level"][:20],
                        message=event["message"][:300],
                        created_at=stamp,
                    )
                )
        server.last_success_at, server.last_error = now, None
        update_alert(server, "connection", None, "")
        evaluate(server, metric)
        aggregate_hour(server.id, metric.collected_at.replace(minute=0, second=0, microsecond=0))
    if server.next_report_at <= now:
        enqueue(server, report(server))
        server.next_report_at = now + timedelta(seconds=setting("report_interval", 14400))
    server.next_check_at = now + timedelta(seconds=server.monitoring_interval)
    server.lease_until = None
    db.session.commit()


def process_server(app, server_id):
    with app.app_context():
        try:
            monitor_one(server_id)
        except Exception as error:
            db.session.rollback()
            logger.error("Перевірку server_id=%s перервано: %s", server_id, type(error).__name__)
        finally:
            db.session.remove()


def claim_server(server_id):
    now = utcnow()
    result = db.session.execute(
        update(Server)
        .where(
            Server.id == server_id,
            Server.is_active.is_(True),
            Server.next_check_at <= now,
            or_(Server.lease_until.is_(None), Server.lease_until < now),
        )
        .values(lease_until=now + timedelta(minutes=10))
    )
    db.session.commit()
    return result.rowcount == 1


def process_job():
    # Claim a pending action and the server lease atomically, preventing concurrent monitoring.
    now = utcnow()
    interrupted = db.session.scalars(
        db.select(Job)
        .join(Server, Job.server_id == Server.id)
        .where(Job.status == "running", Server.lease_until < now)
        .with_for_update(skip_locked=True)
    ).all()
    for item in interrupted:
        item.status, item.finished_at = "failed", now
        item.result = "Операцію перервано. Перевірте віддалений SWAP вручну."
        enqueue(db.session.get(Server, item.server_id), item.result)
    db.session.commit()
    job = db.session.scalar(
        db.select(Job)
        .where(Job.status == "pending")
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not job:
        db.session.rollback()
        return
    result = db.session.execute(
        update(Server)
        .where(
            Server.id == job.server_id, or_(Server.lease_until.is_(None), Server.lease_until < now)
        )
        .values(lease_until=now + timedelta(minutes=10))
    )
    if not result.rowcount:
        db.session.rollback()
        return
    job.status = "running"
    db.session.commit()
    server = db.session.get(Server, job.server_id)
    execute_swap(job, server)
    server.lease_until = None
    server.next_check_at = utcnow()
    db.session.commit()


def main():
    app = create_app()
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    cleanup_at = 0
    futures = set()
    with ThreadPoolExecutor(max_workers=app.config["MONITOR_CONCURRENCY"]) as pool:
        while not stopped.is_set():
            try:
                with app.app_context():
                    futures = {future for future in futures if not future.done()}
                    free = app.config["MONITOR_CONCURRENCY"] - len(futures)
                    if free:
                        process_job()
                    if time.monotonic() >= cleanup_at:
                        cleanup()
                        cleanup_at = time.monotonic() + 3600
                    now = utcnow()
                    ids = (
                        db.session.scalars(
                            db.select(Server.id)
                            .where(
                                Server.is_active.is_(True),
                                Server.next_check_at <= now,
                                or_(Server.lease_until.is_(None), Server.lease_until < now),
                            )
                            .order_by(Server.next_check_at)
                            .limit(free)
                        ).all()
                        if free
                        else []
                    )
                    for server_id in ids:
                        if claim_server(server_id):
                            futures.add(pool.submit(process_server, app, server_id))
            except Exception as error:
                logger.error("Цикл моніторингу: %s", type(error).__name__)
            stopped.wait(3)


if __name__ == "__main__":
    main()
