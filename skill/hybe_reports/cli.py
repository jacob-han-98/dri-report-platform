"""hybe-reports CLI — entry point for the Claude Code skill."""
from __future__ import annotations

import json
import sys
import webbrowser
from datetime import date
from pathlib import Path

import click

from hybe_reports import config as cfg
from hybe_reports.client import Client, HybeError
from hybe_reports.packager import make_zip


# ---------- helpers ----------


def _client_or_die() -> Client:
    c = cfg.load()
    if c is None:
        click.secho(
            "로그인 안 됨. `hybe-reports login` 먼저 실행.", fg="red", err=True
        )
        sys.exit(2)
    return Client(c)


def _slug_from_dir(path: Path) -> str:
    base = path.resolve().name.lower()
    safe = "".join(ch if (ch.isalnum() or ch == "-") else "-" for ch in base).strip("-")
    if not safe:
        safe = "report"
    return f"{safe}-{date.today().isoformat()}"


def _copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _print_report_row(r: dict) -> None:
    click.echo(
        f"  {click.style(r['slug'], fg='cyan'):<30} "
        f"{r['title'][:40]:<40} "
        f"owner={r['owner_email']:<25} "
        f"vis={r['visibility']:<10} "
        f"updated={r['updated_at'][:19]}"
    )


# ---------- root group ----------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="hybe-reports")
def main() -> None:
    """Hybe Reports — Claude Code 리포트 배포 CLI."""


# ---------- login ----------


@main.command()
@click.option("--base-url", prompt="Base URL", help="예: https://reports.hybe.internal")
@click.option(
    "--token",
    prompt="PAT (hybe_pat_...)",
    hide_input=True,
    help="https://reports.hybe.internal/settings/tokens 에서 발급",
)
def login(base_url: str, token: str) -> None:
    """토큰을 OS keyring (또는 ~/.config) 에 저장."""
    where = cfg.save(base_url, token)

    # immediate verification
    client = Client(cfg.Config(base_url=base_url.rstrip("/"), token=token))
    try:
        me = client.whoami()
    except HybeError as e:
        click.secho(f"실패: {e}", fg="red", err=True)
        sys.exit(1)
    finally:
        client.close()

    click.secho(
        f"로그인 OK ({where}). user={me['email']} role={me['role']}",
        fg="green",
    )


@main.command()
def logout() -> None:
    """저장된 토큰 제거."""
    cfg.clear()
    click.echo("토큰 제거됨.")


# ---------- deploy ----------


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=".",
)
@click.option("--slug", help="URL slug. 미지정 시 디렉토리명 + 오늘 날짜.")
@click.option("--title", help="제목. 미지정 시 slug 사용.")
@click.option("--description", help="설명 (선택).")
@click.option(
    "--visibility",
    type=click.Choice(["internal", "restricted"]),
    default="restricted",
    show_default=True,
)
@click.option("--tag", "tags", multiple=True, help="태그 (여러 번 지정 가능).")
@click.option("--entry-point", help="기본 진입 파일 (기본 index.html 자동 탐지).")
def deploy(
    path: Path,
    slug: str | None,
    title: str | None,
    description: str | None,
    visibility: str,
    tags: tuple[str, ...],
    entry_point: str | None,
) -> None:
    """디렉토리를 zip 으로 묶어 업로드. URL 출력 + 클립보드 복사."""
    slug = slug or _slug_from_dir(path)
    title = title or slug
    meta = {
        "slug": slug,
        "title": title,
        "visibility": visibility,
    }
    if description:
        meta["description"] = description
    if tags:
        meta["tags"] = list(tags)
    if entry_point:
        meta["entry_point"] = entry_point

    click.echo(f"packaging {path.resolve()} → zip ...")
    zip_path = make_zip(path)
    try:
        client = _client_or_die()
        try:
            click.echo(f"uploading slug={slug} ...")
            res = client.deploy(zip_path, meta)
        except HybeError as e:
            click.secho(f"실패: {e}", fg="red", err=True)
            sys.exit(1)
        finally:
            client.close()
    finally:
        zip_path.unlink(missing_ok=True)

    url = res["url"]
    click.secho(f"\n✓ 배포 완료 — {url}", fg="green")
    if _copy_to_clipboard(url):
        click.echo("  (URL 클립보드 복사됨)")
    click.echo(
        f"  files={res['file_count']} size={res['size_bytes']}B "
        f"visibility={res['visibility']}"
    )


# ---------- list ----------


@main.command(name="list")
@click.option("--mine", is_flag=True, help="내가 owner 인 것만.")
@click.option("--shared-with-me", "shared", is_flag=True, help="공유받은 것만.")
def list_cmd(mine: bool, shared: bool) -> None:
    """리포트 목록."""
    client = _client_or_die()
    try:
        try:
            me = client.whoami()
            rows = client.list_reports(owner=me["email"] if mine else None)
        except HybeError as e:
            click.secho(f"실패: {e}", fg="red", err=True)
            sys.exit(1)
    finally:
        client.close()

    if shared:
        rows = [r for r in rows if r["owner_email"] != me["email"]]

    if not rows:
        click.echo("(empty)")
        return
    for r in rows:
        _print_report_row(r)


# ---------- info ----------


@main.command()
@click.argument("slug")
def info(slug: str) -> None:
    """리포트 상세 정보."""
    client = _client_or_die()
    try:
        try:
            r = client.get_report(slug)
            viewers = []
            try:
                viewers = client.list_viewers(slug)
            except HybeError:
                pass  # not owner/admin → skip viewers section
        except HybeError as e:
            click.secho(f"실패: {e}", fg="red", err=True)
            sys.exit(1)
    finally:
        client.close()

    click.secho(f"{r['title']}", fg="cyan", bold=True)
    click.echo(f"  url:         {r['url']}")
    click.echo(f"  slug:        {r['slug']}")
    click.echo(f"  owner:       {r['owner_email']}")
    click.echo(f"  visibility:  {r['visibility']}")
    click.echo(f"  entry:       {r['entry_point']}")
    click.echo(f"  description: {r.get('description') or '-'}")
    click.echo(f"  tags:        {', '.join(r.get('tags') or []) or '-'}")
    click.echo(f"  size:        {r['size_bytes']}B  ({r['file_count']} files)")
    click.echo(f"  created:     {r['created_at']}")
    click.echo(f"  updated:     {r['updated_at']}")
    click.echo(f"  views:       {r['view_count']}")
    if viewers:
        click.echo("  viewers:")
        for v in viewers:
            click.echo(f"    - {v['user_email']} (granted by {v['granted_by']})")


# ---------- share ----------


@main.command()
@click.argument("slug")
@click.option("--add", "add_email", help="viewer 추가 (이메일).")
@click.option("--remove", "remove_email", help="viewer 제거 (이메일).")
@click.option(
    "--visibility",
    type=click.Choice(["internal", "restricted"]),
    help="가시성 변경.",
)
def share(
    slug: str,
    add_email: str | None,
    remove_email: str | None,
    visibility: str | None,
) -> None:
    """권한 관리: viewer 추가/제거 또는 visibility 변경."""
    if not (add_email or remove_email or visibility):
        raise click.UsageError(
            "--add EMAIL / --remove EMAIL / --visibility {internal,restricted} 중 하나는 필수"
        )

    client = _client_or_die()
    try:
        try:
            if visibility:
                client.patch_report(slug, visibility=visibility)
                click.secho(f"visibility={visibility} 적용", fg="green")
            if add_email:
                client.add_viewer(slug, add_email)
                click.secho(f"+ viewer {add_email}", fg="green")
            if remove_email:
                client.remove_viewer(slug, remove_email)
                click.secho(f"- viewer {remove_email}", fg="yellow")
        except HybeError as e:
            click.secho(f"실패: {e}", fg="red", err=True)
            sys.exit(1)
    finally:
        client.close()


# ---------- redeploy ----------


@main.command()
@click.argument("slug")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=".",
)
def redeploy(slug: str, path: Path) -> None:
    """slug 의 파일을 새 zip 으로 덮어쓰기 (메타 유지)."""
    click.echo(f"packaging {path.resolve()} → zip ...")
    zip_path = make_zip(path)
    try:
        client = _client_or_die()
        try:
            res = client.redeploy(slug, zip_path)
        except HybeError as e:
            click.secho(f"실패: {e}", fg="red", err=True)
            sys.exit(1)
        finally:
            client.close()
    finally:
        zip_path.unlink(missing_ok=True)
    click.secho(
        f"✓ redeploy 완료 — {res['url']} files={res['file_count']} size={res['size_bytes']}B",
        fg="green",
    )


# ---------- delete ----------


@main.command()
@click.argument("slug")
@click.confirmation_option(prompt="정말 삭제할까요?")
def delete(slug: str) -> None:
    """리포트 삭제."""
    client = _client_or_die()
    try:
        try:
            client.delete_report(slug)
        except HybeError as e:
            click.secho(f"실패: {e}", fg="red", err=True)
            sys.exit(1)
    finally:
        client.close()
    click.secho(f"✓ {slug} 삭제됨", fg="yellow")


# ---------- open ----------


@main.command(name="open")
@click.argument("slug")
def open_cmd(slug: str) -> None:
    """브라우저로 리포트 URL 열기."""
    client = _client_or_die()
    try:
        try:
            r = client.get_report(slug)
        except HybeError as e:
            click.secho(f"실패: {e}", fg="red", err=True)
            sys.exit(1)
    finally:
        client.close()
    url = r["url"]
    click.echo(url)
    webbrowser.open(url)


if __name__ == "__main__":
    main()
