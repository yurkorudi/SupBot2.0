import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, update

from app import create_app
from app.extensions import db
from app.models import (
    Job,
    Server,
    ServerCpuRecovery,
    ServerLog,
    ServerMetric,
    ServerReportSchedule,
    utcnow,
)
from app.services.alert_manager import connection_failed, enqueue, evaluate, setting, update_alert
from app.services.cpu_recovery import evaluate_cpu_recovery, send_reboot
from app.services.database_backups import claim_backup, run_backup
from app.services.installation import expire_installations
from app.services.report_generator import report
from app.services.report_schedule import enqueue_due_reports, enqueue_manual_report
from app.services.security import audit
from app.services.service_inventory import sync_services
from app.services.ssh_client import SSHFailure
from app.services.swap_manager import execute_swap, finish_swap, plan_swap

from .cleanup import aggregate_hour, cleanup
from .collectors import collect_server

logger = logging.getLogger(__name__)


def monitor_one(server_id):
    server = db.session.get(Server, server_id)
    if not server or not (
        server.installation_status == "VERIFYING"
        or (
            server.is_active
            and server.installation_status in ("READY", "ACTIVE", "CONNECTION_FAILED")
        )
    ):
        return
    verifying = server.installation_status == "VERIFYING"
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
    db.session.refresh(
        server,
        attribute_names=[
            "installation_status",
            "is_active",
            "automation_enabled",
            "service_monitoring_enabled",
        ],
        with_for_update=True,
    )
    now = utcnow()
    if server.installation_status not in ("VERIFYING", "READY", "ACTIVE", "CONNECTION_FAILED"):
        server.lease_until = None
        db.session.commit()
        return
    if data is None:
        if server.status != "offline" or server.last_error != failure:
            audit(
                "monitor",
                "ssh_disconnected",
                failure,
                server.id,
                source="automatic",
                status="failed",
            )
        connection_failed(server, failure)
        server.installation_status = "CONNECTION_FAILED"
        if verifying:
            server.installation_error = failure
            server.is_active = False
            audit(
                "monitor",
                "installation_connection_failed",
                failure,
                server.id,
                source="automatic",
                status="failed",
            )
    else:
        if server.status == "offline":
            audit(
                "monitor",
                "ssh_recovered",
                "SSH-з’єднання відновлено",
                server.id,
                source="automatic",
            )
        if server.service_monitoring_enabled:
            sync_services(server, data["details"], now)
        else:
            data["details"]["services"] = {}
        metric = ServerMetric(server_id=server.id, **data)
        db.session.add(metric)
        db.session.flush()
        for event in data["details"].get("errors", [])[:30]:
            stamp = datetime.fromtimestamp(event["timestamp"], timezone.utc).replace(tzinfo=None)
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
        server.installation_status = "ACTIVE" if server.is_authorized else "READY"
        if verifying:
            server.installation_completed_at = now
            server.installation_error = None
            server.is_active = True
            audit(
                "monitor",
                "installation_ready",
                "SSH і збір метрик перевірено",
                server.id,
                source="automatic",
            )
        update_alert(server, "connection", None, "")
        evaluate(server, metric)
        aggregate_hour(server.id, metric.collected_at.replace(minute=0, second=0, microsecond=0))
    schedule = db.session.get(ServerReportSchedule, server.id)
    if schedule and schedule.manual_requested_at is not None:
        enqueue_manual_report(server, schedule)
    elif server.next_report_at <= now and (not schedule or schedule.mode == "interval"):
        if server.is_authorized and server.telegram_group_id:
            enqueue(server, report(server))
        server.next_report_at = now + timedelta(seconds=setting("report_interval", 14400))
    reboot, interval = evaluate_cpu_recovery(server, data, now)
    if data is not None and plan_swap(server, data, now):
        interval = min(interval, 60)
    server.next_check_at = now + timedelta(seconds=interval)
    if not reboot:
        server.lease_until = None
    db.session.commit()
    if reboot:
        send_reboot(server)
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


def eligible_servers():
    return or_(
        Server.installation_status == "VERIFYING",
        and_(
            Server.is_active.is_(True),
            Server.installation_status.in_(["READY", "ACTIVE", "CONNECTION_FAILED"]),
        ),
    )


def claim_server(server_id):
    now = utcnow()
    result = db.session.execute(
        update(Server)
        .where(
            Server.id == server_id,
            eligible_servers(),
            Server.next_check_at <= now,
            or_(Server.lease_until.is_(None), Server.lease_until < now),
        )
        .values(lease_until=now + timedelta(minutes=10))
    )
    db.session.commit()
    return result.rowcount == 1


def claim_swap_job():
    # Claim a pending action and the server lease atomically, preventing concurrent monitoring.
    now = utcnow()
    interrupted = db.session.scalars(
        db.select(Job)
        .join(Server, Job.server_id == Server.id)
        .where(Job.status == "running", Server.lease_until < now)
        .with_for_update(skip_locked=True)
    ).all()
    for item in interrupted:
        finish_swap(
            item,
            db.session.get(Server, item.server_id),
            "failed",
            "Операцію перервано. Перевірте віддалений SWAP вручну.",
        )
    db.session.commit()
    job = db.session.execute(
        db.select(Job.id, Job.server_id)
        .join(Server, Server.id == Job.server_id)
        .where(
            Job.status == "pending",
            or_(Job.run_after.is_(None), Job.run_after <= now),
            or_(Server.lease_until.is_(None), Server.lease_until < now),
            ~db.select(ServerCpuRecovery.server_id)
            .where(
                ServerCpuRecovery.server_id == Job.server_id,
                ServerCpuRecovery.verify_until.is_not(None),
            )
            .exists(),
        )
        .order_by(Job.run_after.is_not(None), Job.id)
        .limit(1)
    ).first()
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
    claimed = db.session.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == "pending",
            or_(Job.run_after.is_(None), Job.run_after <= now),
        )
        .values(status="running")
    )
    if not claimed.rowcount:
        db.session.rollback()
        return
    job_id = job.id
    db.session.commit()
    return job_id


def run_swap_job(job_id):
    job = db.session.get(Job, job_id)
    server = db.session.get(Server, job.server_id)
    data = None
    if not server.is_active or (
        job.telegram_group_id is not None
        and (not server.is_authorized or server.telegram_group_id != job.telegram_group_id)
    ):
        finish_swap(job, server, "cancelled", "SWAP скасовано. Сервер або група більше не активні.")
    if job.status == "running" and (job.source == "automatic" or server.ssh_public_key):
        try:
            data = collect_server(server)
        except SSHFailure:
            finish_swap(job, server, "failed", "Не вдалося повторно перевірити VPS через SSH.")
    if job.status == "running":
        execute_swap(job, server, data)
    server.lease_until = None
    server.next_check_at = utcnow()
    db.session.commit()


def process_swap_job(app, job_id):
    with app.app_context():
        try:
            run_swap_job(job_id)
        except Exception as error:
            db.session.rollback()
            logger.error("Завдання SWAP id=%s: %s", job_id, type(error).__name__)


def main():
    app = create_app()
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    cleanup_at = 0
    setup_cleanup_at = 0
    futures = set()
    backup_future = None
    with (
        ThreadPoolExecutor(max_workers=app.config["MONITOR_CONCURRENCY"]) as pool,
        ThreadPoolExecutor(max_workers=1) as backup_pool,
    ):
        while not stopped.is_set():
            try:
                with app.app_context():
                    futures = {future for future in futures if not future.done()}
                    free = app.config["MONITOR_CONCURRENCY"] - len(futures)
                    if free:
                        job_id = claim_swap_job()
                        if job_id is not None:
                            futures.add(pool.submit(process_swap_job, app, job_id))
                            free -= 1
                    enqueue_due_reports()
                    if backup_future is None or backup_future.done():
                        if backup_future is not None and backup_future.exception():
                            logger.error("Збій worker резервного копіювання.")
                        backup_future = None
                        backup = claim_backup()
                        if backup:
                            backup_future = backup_pool.submit(run_backup, app, *backup)
                    if time.monotonic() >= setup_cleanup_at:
                        expire_installations()
                        setup_cleanup_at = time.monotonic() + 60
                    if time.monotonic() >= cleanup_at:
                        cleanup()
                        cleanup_at = time.monotonic() + 3600
                    now = utcnow()
                    ids = (
                        db.session.scalars(
                            db.select(Server.id)
                            .where(
                                eligible_servers(),
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
