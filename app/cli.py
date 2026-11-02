import io

import click
import pyotp
import qrcode
import qrcode.image.svg
from flask.cli import with_appcontext

from .extensions import db
from .models import User
from .services.encryption import encrypt


def register_commands(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt="Логін")
    @click.password_option(confirmation_prompt=True)
    @with_appcontext
    def create_admin(username, password):
        if not username.strip() or len(username) > 80:
            raise click.ClickException("Некоректний логін.")
        if db.session.scalar(db.select(User).where(User.username == username)):
            raise click.ClickException("Користувач уже існує.")
        user = User(username=username, totp_secret_encrypted=encrypt(pyotp.random_base32()))
        try:
            user.set_password(password)
        except ValueError as error:
            raise click.ClickException(str(error)) from None
        db.session.add(user)
        db.session.commit()
        click.echo(
            "Адміністратора створено. Відкрийте панель через SSH-тунель або HTTPS та налаштуйте TOTP."
        )

    @app.cli.command("reset-totp")
    @click.argument("username")
    def reset_totp(username):
        user = db.session.scalar(db.select(User).where(User.username == username))
        if not user:
            raise click.ClickException("Користувача не знайдено.")
        user.totp_secret_encrypted = encrypt(pyotp.random_base32())
        user.totp_enabled = False
        user.last_totp_step = -1
        db.session.commit()
        click.echo("TOTP скинуто; повторіть налаштування після введення пароля.")


def qr_svg(uri):
    buffer = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    return buffer.getvalue()
