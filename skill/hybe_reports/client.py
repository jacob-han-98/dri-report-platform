"""HTTP client wrapping the Hybe Reports API. Bearer auto-attach + friendly errors."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from hybe_reports.config import Config


class HybeError(Exception):
    """Raised when the API returns a non-2xx response. Carries status + parsed detail."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _check(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        try:
            body = resp.json()
            detail = body.get("detail", body)
        except Exception:
            detail = resp.text or f"<empty body>"

        msg = _humanize(resp.status_code, detail)
        raise HybeError(resp.status_code, msg)

    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _humanize(status: int, detail: object) -> str:
    detail_s = str(detail)
    if status == 401:
        return "토큰이 유효하지 않거나 만료됨. `hybe-reports login` 다시 실행."
    if status == 403:
        return f"권한 없음: {detail_s}"
    if status == 409:
        return f"slug 충돌: {detail_s}. 다른 slug 를 쓰거나 `redeploy` 사용."
    if status == 413:
        return f"파일 크기 초과: {detail_s}"
    return detail_s


class Client:
    def __init__(self, config: Config):
        self._cfg = config
        self._http = httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.token}"},
            verify=config.verify_tls,
            timeout=60.0,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    # ---- introspection ----

    def whoami(self) -> dict:
        r = self._http.get("/auth/check")
        if r.status_code != 200:
            raise HybeError(r.status_code, _humanize(r.status_code, r.text))
        return {
            "email": r.headers.get("x-user-email"),
            "role": r.headers.get("x-user-role"),
        }

    # ---- reports ----

    def list_reports(self, *, owner: str | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if owner:
            params["owner"] = owner
        return _check(self._http.get("/api/reports", params=params))

    def get_report(self, slug: str) -> dict:
        return _check(self._http.get(f"/api/reports/{slug}"))

    def deploy(self, zip_path: Path, meta: dict) -> dict:
        import json as _json

        with zip_path.open("rb") as f:
            files = {"file": (zip_path.name, f, "application/zip")}
            data = {"meta": _json.dumps(meta)}
            return _check(self._http.post("/api/reports", files=files, data=data))

    def redeploy(self, slug: str, zip_path: Path) -> dict:
        with zip_path.open("rb") as f:
            files = {"file": (zip_path.name, f, "application/zip")}
            return _check(self._http.put(f"/api/reports/{slug}", files=files))

    def patch_report(self, slug: str, **fields) -> dict:
        body = {k: v for k, v in fields.items() if v is not None}
        return _check(
            self._http.patch(f"/api/reports/{slug}", json=body)
        )

    def delete_report(self, slug: str) -> None:
        _check(self._http.delete(f"/api/reports/{slug}"))

    # ---- viewers ----

    def list_viewers(self, slug: str) -> list[dict]:
        return _check(self._http.get(f"/api/reports/{slug}/viewers"))

    def add_viewer(self, slug: str, email: str) -> dict:
        return _check(
            self._http.post(
                f"/api/reports/{slug}/viewers",
                json={"user_email": email},
            )
        )

    def remove_viewer(self, slug: str, email: str) -> None:
        _check(self._http.delete(f"/api/reports/{slug}/viewers/{email}"))
